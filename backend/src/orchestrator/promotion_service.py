"""Durable immutable previews and isolated, crash-recoverable Git promotion."""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy import text

from orchestrator.artifacts import ArtifactService
from orchestrator.artifacts.models import (
    ArtifactAccessRequest,
    ArtifactPublishRequest,
    ArtifactReference,
    ArtifactScope,
    ArtifactVersionRecord,
    artifact_storage_key,
)
from orchestrator.artifacts.ports import ArtifactVersionConflictError
from orchestrator.commands import CommandError
from orchestrator.domain import (
    ArtifactRecord,
    AuthenticatedActor,
    AuthorityGrant,
    EventDraft,
    PromotionRecord,
    RecordMetadata,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_preview import ChangedPath
from orchestrator.services.events import DurableEventService, EventWakeupNotifier
from orchestrator.workspace import PostgresWorkspaceImportStore, WorkspaceImport

_VERSION = re.compile(
    r"^(?:v[0-9]+\.[0-9]+\.[0-9]+|run-[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+)$"
)
_SEMVER = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")


class PromotionServiceError(CommandError):
    """A preview or promotion command was refused safely."""


class PromotionGuestGit(Protocol):
    """Narrow read-only guest-Git boundary used by promotion."""

    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]: ...

    def export_patch(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> bytes: ...


class PostgresPromotionService:
    """Owns preview evidence and the only writable host-Git command path."""

    def __init__(
        self,
        *,
        unit_of_work: PostgresUnitOfWork,
        catalog: ProjectCatalog,
        imports: PostgresWorkspaceImportStore,
        guest_git: PromotionGuestGit,
        artifacts: ArtifactService,
        review_root: Path,
        worktree_root: Path | None = None,
        confirmation_secret: str,
        notifier: EventWakeupNotifier,
        confirmation_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if len(confirmation_secret) < 32:
            raise ValueError("promotion confirmation secret is too short")
        if not timedelta(minutes=1) <= confirmation_ttl <= timedelta(hours=1):
            raise ValueError("promotion confirmation TTL is outside range")
        self._unit_of_work = unit_of_work
        self._catalog = catalog
        self._imports = imports
        self._guest_git = guest_git
        self._artifacts = artifacts
        self._review_root = review_root.resolve()
        self._review_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._worktree_root = (worktree_root or review_root / "worktrees").resolve()
        self._worktree_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._secret = confirmation_secret.encode()
        self._confirmation_ttl = confirmation_ttl
        self._events = DurableEventService(unit_of_work, notifier)

    def create_preview(
        self,
        *,
        run_id: str,
        user_id: str,
        checkpoint_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        self._validate_key(idempotency_key)
        workspace = self._owned_workspace(run_id, user_id)
        checkpoint = self._checkpoint(run_id, checkpoint_id)
        durable_key = f"{run_id}:{user_id}:{idempotency_key}"
        existing_by_key = self._preview_by_idempotency(run_id, user_id, durable_key)
        if existing_by_key is not None:
            existing_payload = cast(dict[str, object], existing_by_key["payload"])
            if existing_payload.get("checkpoint_id") != checkpoint["checkpoint_id"]:
                raise PromotionServiceError("promotion_preview_idempotency_conflict")
            return self._preview_projection(existing_by_key, user_id)
        changed = self._guest_git.diff_paths(
            workspace, workspace.baseline.commit_hash, str(checkpoint["commit_hash"])
        )
        patch = self._guest_git.export_patch(
            workspace, workspace.baseline.commit_hash, str(checkpoint["commit_hash"])
        )
        patch_hash = sha256(patch).hexdigest()
        source = self._catalog.preview(workspace.project_id)
        tags = self._git_tags(self._catalog.resolve(workspace.project_id))
        checks, issues, reasons = self._preview_gates(
            run_id=run_id,
            workspace=workspace,
            checkpoint=checkpoint,
            changed_paths=tuple(item.path for item in changed),
            current_head=source.git_head,
            current_dirty=source.git_dirty,
            current_fingerprint=source.source_fingerprint,
        )
        proposed_version = self._propose_version(tags, run_id)
        canonical = {
            "run_id": run_id,
            "workspace_id": workspace.workspace_id,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_commit": checkpoint["commit_hash"],
            "patch_sha256": patch_hash,
            "changed_files": [item.path for item in changed],
            "checks": checks,
            "issues": issues,
            "baseline": workspace.source_git_head or workspace.source_fingerprint,
            "recorded_baseline": workspace.source_git_head
            or workspace.source_fingerprint,
            "current_baseline": source.git_head or source.source_fingerprint,
            "current_source_dirty": source.git_dirty,
            "protected_paths": sorted(
                set(workspace.protected_paths) | set(workspace.excluded_paths)
            ),
            "proposed_version": proposed_version,
            "target_branch": (
                f"orchestrator/{proposed_version}-{run_id.removeprefix('run_')}"
            ),
            "direct_eligible": not reasons,
            "conflict_reason": ", ".join(reasons) if reasons else None,
        }
        preview_hash = sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        existing = self._preview_by_hash(run_id, user_id, preview_hash)
        if existing is not None:
            return self._preview_projection(existing, user_id)

        artifact_id = f"art_promotion_{preview_hash[:24]}"
        artifact = self._publish_patch(run_id, artifact_id, patch, patch_hash)
        now = datetime.now(UTC)
        nonce = self._nonce(preview_hash, user_id)
        expires_at = now + self._confirmation_ttl
        payload = {
            **canonical,
            "preview_hash": preview_hash,
            "artifact_id": artifact_id,
            "artifact_version": artifact.version,
        }
        preview_id = f"preview_{preview_hash[:24]}"
        key = f"promotion-preview:{preview_hash}"
        draft = self._event_draft(
            run_id,
            key=key,
            event_type="promotion.previewed",
            status="accepted",
            summary="Immutable promotion preview recorded",
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            if unit_of_work.artifacts.get(artifact_id) is None:
                unit_of_work.artifacts.add(
                    ArtifactRecord(
                        artifact_id=artifact_id,
                        run_id=run_id,
                        logical_name="promotion-patch",
                        version=artifact.version,
                        media_type=artifact.media_type,
                        storage_locator=artifact.storage_key,
                        sha256=artifact.content_sha256,
                        producer=self._service_actor(now),
                        access_policy=("operator", "promotion-manager"),
                        metadata=self._metadata(now, f"{key}:artifact"),
                    )
                )
            unit_of_work.connection.execute(
                text(
                    "INSERT INTO promotion_previews (preview_id, run_id, workspace_id, "
                    "artifact_id, artifact_version, manifest, created_at, preview_hash, "
                    "checkpoint_id, payload, direct_eligible, idempotency_key, "
                    "confirmation_nonce_digest, confirmation_expires_at) VALUES ("
                    ":preview_id, :run_id, :workspace_id, :artifact_id, :artifact_version, "
                    "CAST(:manifest AS jsonb), :now, :preview_hash, :checkpoint_id, "
                    "CAST(:payload AS jsonb), :eligible, :idempotency_key, "
                    ":nonce_digest, :expires_at) ON CONFLICT DO NOTHING"
                ),
                {
                    "preview_id": preview_id,
                    "run_id": run_id,
                    "workspace_id": workspace.workspace_id,
                    "artifact_id": artifact_id,
                    "artifact_version": artifact.version,
                    "manifest": json.dumps(canonical["changed_files"]),
                    "now": now,
                    "preview_hash": preview_hash,
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "payload": json.dumps(payload, separators=(",", ":")),
                    "eligible": not reasons,
                    "idempotency_key": durable_key,
                    "nonce_digest": sha256(nonce.encode()).hexdigest(),
                    "expires_at": expires_at,
                },
            )

        self._events.apply(draft, persist)
        stored = self._preview_by_hash(run_id, user_id, preview_hash)
        if stored is None:
            stored_by_key = self._preview_by_idempotency(run_id, user_id, durable_key)
            if stored_by_key is not None:
                raise PromotionServiceError("promotion_preview_idempotency_conflict")
            raise PromotionServiceError("promotion_preview_not_recorded")
        return self._preview_projection(stored, user_id)

    def current(self, *, run_id: str, user_id: str) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT preview.payload, preview.confirmation_expires_at FROM "
                        "promotion_previews AS preview JOIN runs USING (run_id) "
                        "WHERE preview.run_id = :run_id AND runs.user_id = :user_id "
                        "ORDER BY preview.created_at DESC, preview.preview_id DESC LIMIT 1"
                    ),
                    {"run_id": run_id, "user_id": user_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise PromotionServiceError("promotion_preview_not_found")
        return self._preview_projection(dict(row), user_id)

    def list_promotions(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._require_owned_run(run_id, user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            rows = unit_of_work.connection.execute(
                text(
                    "SELECT payload FROM promotions WHERE run_id = :run_id "
                    "ORDER BY created_at, promotion_id"
                ),
                {"run_id": run_id},
            ).scalars()
            return {"promotions": [cast(dict[str, object], value) for value in rows]}

    def confirm(
        self,
        *,
        run_id: str,
        user_id: str,
        preview_hash: str,
        confirm_preview_hash: str,
        confirmation_nonce: str,
        version: str,
        message: str,
        create_tag: bool,
        idempotency_key: str,
    ) -> dict[str, object]:
        self._validate_key(idempotency_key)
        message = message.strip()
        if (
            preview_hash != confirm_preview_hash
            or re.fullmatch(r"[0-9a-f]{64}", preview_hash) is None
            or _VERSION.fullmatch(version) is None
            or not message
            or len(message) > 512
        ):
            raise PromotionServiceError("invalid_promotion_confirmation")
        row = self._preview_by_hash(run_id, user_id, preview_hash)
        if row is None:
            raise PromotionServiceError("promotion_preview_not_found")
        expires_at = cast(datetime, row["confirmation_expires_at"])
        expected_nonce = self._nonce(preview_hash, user_id)
        if (
            expires_at <= datetime.now(UTC)
            or not hmac.compare_digest(expected_nonce, confirmation_nonce)
            or not hmac.compare_digest(
                sha256(confirmation_nonce.encode()).hexdigest(),
                str(row["confirmation_nonce_digest"]),
            )
        ):
            raise PromotionServiceError("promotion_confirmation_expired")
        payload = cast(dict[str, object], row["payload"])
        promotion_id = (
            "promotion_"
            + sha256(f"{run_id}\0{idempotency_key}".encode()).hexdigest()[:32]
        )
        confirmation_fingerprint = self._confirmation_fingerprint(
            preview_hash=preview_hash,
            version=version,
            message=message,
            create_tag=create_tag,
        )
        existing = self._promotion(promotion_id)
        if existing is not None:
            if existing.confirmation_fingerprint != confirmation_fingerprint:
                raise PromotionServiceError("promotion_idempotency_conflict")
            if existing.status in {"committed", "rejected", "failed"}:
                return self._promotion_projection(existing)
            if existing.status != "confirmed":
                raise PromotionServiceError("promotion_state_conflict")
        workspace = self._owned_workspace(run_id, user_id)
        branch = f"orchestrator/{version}-{run_id.removeprefix('run_')}"
        now = datetime.now(UTC)
        actor = self._human_actor(user_id, now)
        if existing is None and not bool(payload["direct_eligible"]):
            patch = self._read_patch(run_id, payload)
            review_id, review_commit = self._commit_review_bundle(
                promotion_id=promotion_id,
                payload=payload,
                patch=patch,
                reason=str(payload.get("conflict_reason") or "ineligible preview"),
            )
            rejected = PromotionRecord(
                promotion_id=promotion_id,
                run_id=run_id,
                workspace_id=workspace.workspace_id,
                preview_artifact_id=str(payload["artifact_id"]),
                confirmed_artifact_version=int(cast(int, payload["artifact_version"])),
                target_branch=branch,
                commit_message=message,
                confirmation_fingerprint=confirmation_fingerprint,
                review_repository_id=review_id,
                review_commit=review_commit,
                status="rejected",
                decided_by=actor,
                authority=AuthorityGrant(
                    scope="promotion",
                    source="authenticated-confirmation",
                    granted_at=now,
                ),
                result_summary="Immutable preview is not eligible for direct promotion",
                metadata=self._metadata(now, f"promotion:{promotion_id}"),
            )
            self._record_promotion(
                rejected,
                event_type="promotion.rejected",
                status="rejected",
                summary="Host promotion rejected by immutable preview gates",
            )
            return self._promotion_projection(rejected)

        if existing is None:
            confirmed = PromotionRecord(
                promotion_id=promotion_id,
                run_id=run_id,
                workspace_id=workspace.workspace_id,
                preview_artifact_id=str(payload["artifact_id"]),
                confirmed_artifact_version=int(cast(int, payload["artifact_version"])),
                target_branch=branch,
                target_tag=version if create_tag else None,
                commit_message=message,
                confirmation_fingerprint=confirmation_fingerprint,
                status="confirmed",
                decided_by=actor,
                authority=AuthorityGrant(
                    scope="promotion",
                    source="authenticated-confirmation",
                    granted_at=now,
                ),
                result_summary="Authenticated immutable promotion preview confirmed",
                metadata=self._metadata(now, f"promotion:{promotion_id}"),
            )
            self._record_promotion(
                confirmed,
                event_type="promotion.confirmed",
                status="accepted",
                summary="Authenticated host promotion confirmed",
            )
        else:
            confirmed = existing
        patch = self._read_patch(run_id, payload)
        commit = self._apply_or_recover(
            workspace=workspace,
            branch=confirmed.target_branch,
            message=confirmed.commit_message or message,
            tag=confirmed.target_tag,
            patch=patch,
            expected_patch_hash=str(payload["patch_sha256"]),
        )
        committed_at = datetime.now(UTC)
        committed = confirmed.model_copy(
            update={
                "target_commit": commit,
                "status": "committed",
                "result_summary": "Isolated host branch and commit created successfully",
                "metadata": RecordMetadata(
                    record_version=2,
                    created_at=confirmed.metadata.created_at,
                    updated_at=committed_at,
                    idempotency_key=f"promotion-commit:{promotion_id}",
                    trace_id=confirmed.metadata.trace_id,
                ),
            }
        )
        self._record_promotion(
            committed,
            previous=confirmed,
            event_type="promotion.committed",
            status="completed",
            summary="Isolated host branch and commit recorded",
        )
        return self._promotion_projection(committed)

    def _preview_gates(
        self,
        *,
        run_id: str,
        workspace: WorkspaceImport,
        checkpoint: dict[str, object],
        changed_paths: tuple[str, ...],
        current_head: str | None,
        current_dirty: bool | None,
        current_fingerprint: str,
    ) -> tuple[list[dict[str, str]], list[str], list[str]]:
        with self._unit_of_work.transaction() as unit_of_work:
            completed = bool(
                unit_of_work.connection.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM run_completions WHERE run_id = :run_id)"
                    ),
                    {"run_id": run_id},
                ).scalar_one()
            )
            owner = unit_of_work.connection.execute(
                text(
                    "SELECT owner FROM workspace_input_ownership WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar()
            issue_rows = unit_of_work.connection.execute(
                text(
                    "SELECT issue_id FROM issues WHERE run_id = :run_id "
                    "AND COALESCE((payload ->> 'blocking')::boolean, false)"
                ),
                {"run_id": run_id},
            ).scalars()
            issues = [str(value) for value in issue_rows]
        checks = [
            {
                "name": "outcome-verification",
                "status": "passed" if completed else "failed",
            },
            {
                "name": "user-accepted-checkpoint",
                "status": "passed"
                if checkpoint["kind"] == "user_accepted"
                else "failed",
            },
            {
                "name": "workspace-paused",
                "status": "passed" if owner == "USER" else "failed",
            },
        ]
        reasons: list[str] = []
        if workspace.source_git_head is None:
            reasons.append("source is not a Git repository")
        if workspace.source_git_dirty is not False:
            reasons.append("recorded source baseline was not clean")
        if current_dirty is not False:
            reasons.append("current source checkout is not clean")
        if current_head != workspace.source_git_head:
            reasons.append("source HEAD changed after import")
        if current_fingerprint != workspace.source_fingerprint:
            reasons.append("source content changed after import")
        if not completed:
            reasons.append("outcome verification is incomplete")
        if checkpoint["kind"] != "user_accepted":
            reasons.append("checkpoint is not user accepted")
        if owner != "USER":
            reasons.append("guest mutation is not paused under user ownership")
        if issues:
            reasons.append("blocking issues remain unresolved")
        if not changed_paths:
            reasons.append("preview contains no changed files")
        protected = tuple(workspace.protected_paths) + tuple(workspace.excluded_paths)
        if any(self._protected(path, protected) for path in changed_paths):
            reasons.append("protected or excluded path changed")
        return checks, issues, reasons

    def _apply_or_recover(
        self,
        *,
        workspace: WorkspaceImport,
        branch: str,
        message: str,
        tag: str | None,
        patch: bytes,
        expected_patch_hash: str,
    ) -> str:
        source = self._catalog.resolve(workspace.project_id)
        if self._git(source, "rev-parse", "HEAD") != workspace.source_git_head:
            raise PromotionServiceError("source_baseline_changed")
        if self._git(source, "status", "--porcelain"):
            raise PromotionServiceError("source_baseline_changed")
        existing = self._git(
            source, "show-ref", "--hash", "--verify", f"refs/heads/{branch}", fail=False
        )
        if existing is not None:
            produced = self._git(
                source,
                "diff",
                "--binary",
                "--full-index",
                "--no-renames",
                workspace.source_git_head or "HEAD",
                branch,
                binary=True,
            )
            if sha256(cast(bytes, produced)).hexdigest() != expected_patch_hash:
                raise PromotionServiceError("promotion_branch_conflict")
            if tag:
                tagged = self._git(source, "rev-list", "-n", "1", tag, fail=False)
                if tagged is None:
                    self._git(
                        source,
                        "-c",
                        "user.name=orchestrator-service",
                        "-c",
                        "user.email=service@orchestrator.invalid",
                        "tag",
                        "-a",
                        tag,
                        str(existing),
                        "-m",
                        f"Promotion {tag}",
                    )
                elif tagged != existing:
                    raise PromotionServiceError("promotion_tag_conflict")
            return str(existing)
        if (
            tag
            and self._git(
                source, "show-ref", "--verify", f"refs/tags/{tag}", fail=False
            )
            is not None
        ):
            raise PromotionServiceError("promotion_tag_conflict")
        review = self._worktree_root / sha256(branch.encode()).hexdigest()[:24]
        if review.exists():
            raise PromotionServiceError("stale_promotion_worktree")
        self._git(source, "worktree", "add", "-b", branch, os.fspath(review), "HEAD")
        committed = False
        try:
            applied = subprocess.run(
                ("git", "-C", os.fspath(review), "apply", "--index"),
                input=patch,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
            if applied.returncode:
                raise PromotionServiceError("promotion_patch_apply_failed")
            self._git(review, "diff", "--cached", "--check")
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
            commit = cast(str, self._git(review, "rev-parse", "HEAD"))
            if tag:
                self._git(
                    review,
                    "-c",
                    "user.name=orchestrator-service",
                    "-c",
                    "user.email=service@orchestrator.invalid",
                    "tag",
                    "-a",
                    tag,
                    "-m",
                    f"Promotion {tag}",
                )
            committed = True
            return commit
        finally:
            self._git(
                source, "worktree", "remove", "--force", os.fspath(review), fail=False
            )
            if not committed:
                self._git(source, "branch", "-D", branch, fail=False)
                if review.exists():
                    shutil.rmtree(review, ignore_errors=True)

    def _commit_review_bundle(
        self,
        *,
        promotion_id: str,
        payload: dict[str, object],
        patch: bytes,
        reason: str,
    ) -> tuple[str, str]:
        review_id = f"review_{sha256(promotion_id.encode()).hexdigest()[:24]}"
        target = (self._review_root / review_id).resolve()
        if not target.is_relative_to(self._review_root):
            raise PromotionServiceError("review_repository_path_escape")
        if target.exists():
            return review_id, self._verify_review_bundle(target, patch)
        stage = Path(tempfile.mkdtemp(prefix=f".{review_id}.", dir=self._review_root))
        try:
            (stage / "preview.patch").write_bytes(patch)
            manifest = {
                "preview_hash": payload["preview_hash"],
                "patch_sha256": payload["patch_sha256"],
                "checkpoint_id": payload["checkpoint_id"],
                "checkpoint_commit": payload["checkpoint_commit"],
                "changed_files": payload["changed_files"],
                "checks": payload["checks"],
                "issues": payload["issues"],
                "baseline": payload["baseline"],
                "reason": reason,
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            (stage / "README.md").write_text(
                "# Orchestrator review result\n\n"
                "This service-authored repository preserves an immutable patch and "
                "manifest for a result that was not eligible for direct source "
                "promotion. Review and apply it manually.\n",
                encoding="utf-8",
            )
            self._git(stage, "init", "--quiet", "--initial-branch=review")
            self._git(stage, "add", "README.md", "manifest.json", "preview.patch")
            self._git(
                stage,
                "-c",
                "user.name=orchestrator-service",
                "-c",
                "user.email=service@orchestrator.invalid",
                "commit",
                "--quiet",
                "-m",
                f"Preserve ineligible result {promotion_id}",
            )
            try:
                stage.rename(target)
            except FileExistsError:
                return review_id, self._verify_review_bundle(target, patch)
            return review_id, cast(str, self._git(target, "rev-parse", "HEAD"))
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _verify_review_bundle(target: Path, patch: bytes) -> str:
        try:
            stored = (target / "preview.patch").read_bytes()
        except OSError as error:
            raise PromotionServiceError("review_repository_conflict") from error
        if not hmac.compare_digest(sha256(stored).digest(), sha256(patch).digest()):
            raise PromotionServiceError("review_repository_conflict")
        commit = PostgresPromotionService._git(target, "rev-parse", "HEAD", fail=False)
        if not isinstance(commit, str):
            raise PromotionServiceError("review_repository_conflict")
        return commit

    def _record_promotion(
        self,
        record: PromotionRecord,
        *,
        event_type: Literal[
            "promotion.confirmed", "promotion.committed", "promotion.rejected"
        ],
        status: Literal["accepted", "completed", "rejected"],
        summary: str,
        previous: PromotionRecord | None = None,
    ) -> None:
        key = f"{event_type}:{record.promotion_id}"
        draft = self._event_draft(
            record.run_id,
            key=key,
            event_type=event_type,
            status=status,
            summary=summary,
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            if previous is None:
                if unit_of_work.promotions.get(record.promotion_id) is None:
                    unit_of_work.promotions.add(record)
            else:
                unit_of_work.promotions.compare_and_swap(
                    record,
                    expected_record_version=previous.metadata.record_version,
                )

        self._events.apply(draft, persist)

    def _publish_patch(
        self, run_id: str, artifact_id: str, patch: bytes, digest: str
    ) -> ArtifactVersionRecord:
        try:
            return self._artifacts.publish(
                ArtifactPublishRequest(
                    artifact_id=artifact_id,
                    scope=ArtifactScope(
                        tenant_id="tenant_local",
                        run_id=run_id,
                        allowed_roles=("operator", "promotion-manager"),
                    ),
                    media_type="text/x-diff",
                    expected_version=0,
                    expected_sha256=digest,
                ),
                patch,
            )
        except ArtifactVersionConflictError:
            result = self._artifacts.read(
                ArtifactReference(artifact_id=artifact_id, version=1),
                ArtifactAccessRequest(
                    tenant_id="tenant_local", run_id=run_id, role="promotion-manager"
                ),
            )
            if result.metadata.content_sha256 != digest:
                raise PromotionServiceError("promotion_artifact_conflict") from None
            metadata = result.metadata
            return ArtifactVersionRecord(
                artifact_id=metadata.artifact_id,
                version=metadata.version,
                scope=metadata.scope,
                media_type=metadata.media_type,
                content_sha256=metadata.content_sha256,
                size_bytes=metadata.size_bytes,
                storage_key=artifact_storage_key(
                    metadata.artifact_id,
                    metadata.version,
                    metadata.content_sha256,
                ),
                created_at=metadata.created_at,
            )

    def _read_patch(self, run_id: str, payload: dict[str, object]) -> bytes:
        result = self._artifacts.read(
            ArtifactReference(
                artifact_id=str(payload["artifact_id"]),
                version=int(cast(int, payload["artifact_version"])),
            ),
            ArtifactAccessRequest(
                tenant_id="tenant_local", run_id=run_id, role="promotion-manager"
            ),
        )
        patch = result.content
        if sha256(patch).hexdigest() != payload["patch_sha256"]:
            raise PromotionServiceError("promotion_artifact_conflict")
        return patch

    def _owned_workspace(self, run_id: str, user_id: str) -> WorkspaceImport:
        self._require_owned_run(run_id, user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            workspace_id = unit_of_work.connection.execute(
                text(
                    "SELECT workspace_id FROM workspace_sessions WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar()
        workspace = self._imports.get(str(workspace_id)) if workspace_id else None
        if workspace is None:
            raise PromotionServiceError("workspace_not_ready")
        return workspace

    def _checkpoint(self, run_id: str, checkpoint_id: str | None) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            if checkpoint_id is None:
                row = unit_of_work.connection.execute(
                    text(
                        "SELECT payload FROM workspace_checkpoints WHERE run_id = :run_id "
                        "AND checkpoint_kind = 'user_accepted' "
                        "ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1"
                    ),
                    {"run_id": run_id},
                ).scalar()
            else:
                row = unit_of_work.connection.execute(
                    text(
                        "SELECT payload FROM workspace_checkpoints WHERE run_id = :run_id "
                        "AND checkpoint_id = :checkpoint_id"
                    ),
                    {"run_id": run_id, "checkpoint_id": checkpoint_id},
                ).scalar()
        if not isinstance(row, dict):
            raise PromotionServiceError("promotion_checkpoint_not_found")
        return cast(dict[str, object], row)

    def _require_owned_run(self, run_id: str, user_id: str) -> None:
        with self._unit_of_work.transaction() as unit_of_work:
            found = unit_of_work.connection.execute(
                text(
                    "SELECT run_id FROM runs WHERE run_id = :run_id AND user_id = :user_id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).scalar()
        if found is None:
            raise PromotionServiceError("run_not_found")

    def _preview_by_hash(
        self, run_id: str, user_id: str, preview_hash: str
    ) -> dict[str, object] | None:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT preview.payload, preview.confirmation_nonce_digest, "
                        "preview.confirmation_expires_at FROM promotion_previews AS preview "
                        "JOIN runs USING (run_id) WHERE preview.run_id = :run_id "
                        "AND preview.preview_hash = :preview_hash AND runs.user_id = :user_id"
                    ),
                    {
                        "run_id": run_id,
                        "preview_hash": preview_hash,
                        "user_id": user_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    def _preview_by_idempotency(
        self, run_id: str, user_id: str, idempotency_key: str
    ) -> dict[str, object] | None:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT preview.payload, preview.confirmation_nonce_digest, "
                        "preview.confirmation_expires_at FROM promotion_previews AS preview "
                        "JOIN runs USING (run_id) WHERE preview.run_id = :run_id AND "
                        "preview.idempotency_key = :idempotency_key AND runs.user_id = :user_id"
                    ),
                    {
                        "run_id": run_id,
                        "idempotency_key": idempotency_key,
                        "user_id": user_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    @staticmethod
    def _promotion_projection(record: PromotionRecord) -> dict[str, object]:
        return {
            "status": "fallback" if record.status == "rejected" else record.status,
            "branch": record.target_branch if record.status == "committed" else None,
            "commit_hash": record.target_commit,
            "tag": record.target_tag if record.status == "committed" else None,
            "reason": record.result_summary if record.status == "rejected" else None,
            "review_repository_id": record.review_repository_id,
            "review_commit": record.review_commit,
        }

    def _preview_projection(
        self, row: dict[str, object], user_id: str
    ) -> dict[str, object]:
        payload = cast(dict[str, object], row["payload"])
        return {
            **payload,
            "confirmation_nonce": self._nonce(str(payload["preview_hash"]), user_id),
            "confirmation_expires_at": cast(
                datetime, row["confirmation_expires_at"]
            ).isoformat(),
        }

    def _promotion(self, promotion_id: str) -> PromotionRecord | None:
        with self._unit_of_work.transaction() as unit_of_work:
            return unit_of_work.promotions.get(promotion_id)

    def _event_draft(
        self,
        run_id: str,
        *,
        key: str,
        event_type: Literal[
            "promotion.previewed",
            "promotion.confirmed",
            "promotion.committed",
            "promotion.rejected",
        ],
        status: Literal["accepted", "completed", "rejected"],
        summary: str,
    ) -> EventDraft:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT conversation_id, payload FROM runs WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
            design_version = unit_of_work.connection.execute(
                text(
                    "SELECT COALESCE(MAX(design_version), 1) FROM design_revisions "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
        digest = sha256(key.encode()).hexdigest()
        event_id = f"evt_{digest[:32]}"
        trace_id = row["payload"]["metadata"].get("trace_id") or digest[:32]
        return EventDraft(
            event_id=event_id,
            run_id=run_id,
            conversation_id=str(row["conversation_id"]),
            occurred_at=datetime.now(UTC),
            type=event_type,
            stage="RESUME_GATE",
            node_id="promotion-service",
            attempt_id=f"attempt_{digest[:32]}",
            design_version=int(design_version),
            packet_version=1,
            actor_role="promotion-service",
            status=status,
            outcome=status,
            summary=summary,
            detail_ref=f"/api/v1/runs/{run_id}/events/{event_id}/detail",
            correlation_id=key,
            trace_id=str(trace_id),
            span_id=digest[:16],
            command_idempotency_key=key,
        )

    def _nonce(self, preview_hash: str, user_id: str) -> str:
        return hmac.new(
            self._secret, f"{preview_hash}\0{user_id}".encode(), sha256
        ).hexdigest()

    @staticmethod
    def _confirmation_fingerprint(
        *,
        preview_hash: str,
        version: str,
        message: str,
        create_tag: bool,
    ) -> str:
        canonical = json.dumps(
            {
                "preview_hash": preview_hash,
                "version": version,
                "message": message,
                "create_tag": create_tag,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _metadata(now: datetime, key: str) -> RecordMetadata:
        return RecordMetadata(
            record_version=1,
            created_at=now,
            updated_at=now,
            idempotency_key=key,
            trace_id=sha256(key.encode()).hexdigest()[:32],
        )

    @staticmethod
    def _service_actor(now: datetime) -> AuthenticatedActor:
        return AuthenticatedActor(
            actor_id="service_promotion_manager",
            kind="service",
            role="promotion-manager",
            authenticated_at=now,
            authentication_context="trusted-promotion-boundary",
        )

    @staticmethod
    def _human_actor(user_id: str, now: datetime) -> AuthenticatedActor:
        return AuthenticatedActor(
            actor_id=user_id,
            kind="human",
            role="promotion-authority",
            authenticated_at=now,
            authentication_context="api-authentication-boundary",
        )

    @staticmethod
    def _protected(path: str, protected: tuple[str, ...]) -> bool:
        return any(
            path == item or path.startswith(item.rstrip("/") + "/")
            for item in protected
        )

    @staticmethod
    def _propose_version(tags: tuple[str, ...], run_id: str) -> str:
        parsed = [
            tuple(map(int, match.groups()))
            for tag in tags
            if (match := _SEMVER.fullmatch(tag))
        ]
        if parsed:
            major, minor, _ = max(parsed)
            return f"v{major}.{minor + 1}.0"
        return f"run-{datetime.now(UTC).date().isoformat()}-{run_id.removeprefix('run_')[:12]}"

    @staticmethod
    def _git_tags(source: Path) -> tuple[str, ...]:
        output = PostgresPromotionService._git(source, "tag", "--list")
        return tuple(str(output).splitlines())

    @staticmethod
    def _git(
        path: Path,
        *args: str,
        fail: bool = True,
        binary: bool = False,
    ) -> str | bytes | None:
        completed = subprocess.run(
            ("git", "-C", os.fspath(path), *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        if completed.returncode:
            if fail:
                raise PromotionServiceError("git_operation_failed")
            return None
        return completed.stdout if binary else completed.stdout.decode().strip()

    @staticmethod
    def _validate_key(value: str) -> None:
        if not value or len(value) > 256:
            raise PromotionServiceError("missing_idempotency_key")
