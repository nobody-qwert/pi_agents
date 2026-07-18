"""End-to-end proof for immutable preview and isolated durable promotion."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import ArtifactPolicy
from orchestrator.checkpoints import (
    LocalGuestCheckpointAdapter,
    PostgresCheckpointService,
)
from orchestrator.commands import CommandError, PostgresRunCommandService
from orchestrator.desktop_api import PostgresDesktopService
from orchestrator.domain import (
    AuthenticatedActor,
    AuthorityGrant,
    RecordMetadata,
    RunCompletionRecord,
)
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_preview import LocalGuestPreviewAdapter
from orchestrator.promotion_service import PostgresPromotionService
from orchestrator.sse import PostgresEventStreamStore, SseEventService
from orchestrator.vm import PostgresVmLifecycleService
from orchestrator.workspace import (
    LocalGuestWorkspaceAdapter,
    PostgresWorkspaceImportStore,
    WorkspaceImportService,
)


class ReadyGateway:
    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status="ready", configured_model_id="qwen3.6-27b")

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        raise AssertionError("promotion setup does not invoke the model")


class RecordingVmAdapter:
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        return None

    def probe_ready(self, guest_id: str) -> bool:
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        return None


class NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        return None


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(("git", "-C", path, *args), text=True).strip()


def _service(
    *,
    unit_of_work: PostgresUnitOfWork,
    database_url: str,
    catalog: ProjectCatalog,
    imports: PostgresWorkspaceImportStore,
    guest_root: Path,
    artifact_root: Path,
    review_root: Path,
) -> tuple[PostgresPromotionService, PostgresArtifactMetadataRepository]:
    metadata = PostgresArtifactMetadataRepository(database_url)
    artifacts = ArtifactService(
        content_store=LocalVolumeArtifactStore(artifact_root),
        metadata_repository=metadata,
        policy=ArtifactPolicy(),
    )
    return (
        PostgresPromotionService(
            unit_of_work=unit_of_work,
            catalog=catalog,
            imports=imports,
            guest_git=LocalGuestPreviewAdapter(guest_root),
            artifacts=artifacts,
            review_root=review_root,
            confirmation_secret="test-promotion-secret-000000000000000000000000",
            notifier=NoopNotifier(),
        ),
        metadata,
    )


def _confirm(
    service: PostgresPromotionService,
    *,
    run_id: str,
    preview: dict[str, object],
    message: str = "Promote the verified result",
) -> dict[str, object]:
    return service.confirm(
        run_id=run_id,
        user_id="user_promotion",
        preview_hash=str(preview["preview_hash"]),
        confirm_preview_hash=str(preview["preview_hash"]),
        confirmation_nonce=str(preview["confirmation_nonce"]),
        version="v1.1.0",
        message=message,
        create_tag=True,
        idempotency_key="confirm-once",
    )


def test_promotion_recovers_confirmed_intent_without_mutating_checkout(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "sources"
    source = source_root / "project"
    source.mkdir(parents=True)
    (source / "main.txt").write_text("base\n")
    subprocess.run(("git", "init", "--quiet", source), check=True)
    subprocess.run(("git", "-C", source, "add", "."), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            source,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "base",
        ),
        check=True,
    )
    subprocess.run(("git", "-C", source, "tag", "v1.0.0"), check=True)
    original_head = _git(source, "rev-parse", "HEAD")

    catalog = ProjectCatalog((source_root,))
    project = catalog.discover()[0]
    run = PostgresRunCommandService(catalog, ReadyGateway(), postgres_uow).create(
        user_id="user_promotion",
        project_id=project.project_id,
        message="Produce an isolated promotable change",
        idempotency_key="promotion-run",
    )
    lifecycle = PostgresVmLifecycleService(RecordingVmAdapter(), postgres_uow)
    lifecycle.create(run.run_id)
    lifecycle.probe(run.run_id)
    guest_root = tmp_path / "guest"
    import_store = PostgresWorkspaceImportStore(postgres_uow)
    imports = WorkspaceImportService(
        catalog,
        lifecycle,
        LocalGuestWorkspaceAdapter(guest_root),
        import_store,
    )
    workspace_id = lifecycle.workspace_id(run.run_id)
    workspace = imports.import_snapshot(
        workspace_id=workspace_id,
        run_id=run.run_id,
        project_id=project.project_id,
        expected_source_fingerprint=project.source_fingerprint,
    )
    guest_path = guest_root / workspace.guest_id / workspace.guest_path
    (guest_path / "main.txt").write_text("promoted\n")

    desktop = PostgresDesktopService(
        postgres_uow, "test-desktop-session-secret-000000000000"
    )
    desktop.issue_session(
        run_id=run.run_id,
        user_id="user_promotion",
        idempotency_key="promotion-desktop",
    )
    desktop.change_owner(
        run_id=run.run_id,
        user_id="user_promotion",
        requested_owner="USER",
        idempotency_key="promotion-pause",
    )
    checkpoints = PostgresCheckpointService(
        imports,
        LocalGuestCheckpointAdapter(guest_root),
        postgres_uow,
        NoopNotifier(),
    )
    checkpoint = checkpoints.create(
        workspace_id=workspace_id,
        checkpoint_id="checkpoint_user_promotion",
        kind="user_accepted",
        design_version=1,
    )
    now = datetime.now(UTC)
    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.run_completions.add(
            RunCompletionRecord(
                completion_id="completion_promotion",
                run_id=run.run_id,
                outcome_evidence_ids=(),
                completed_at=now,
                completed_by=AuthenticatedActor(
                    actor_id="service_promotion_test",
                    kind="service",
                    role="outcome-verifier",
                    authenticated_at=now,
                    authentication_context="integration-test",
                ),
                authority=AuthorityGrant(
                    scope="completion",
                    source="integration-test",
                    granted_at=now,
                ),
                summary="All test acceptance criteria passed",
                metadata=RecordMetadata(
                    record_version=1,
                    created_at=now,
                    updated_at=now,
                    idempotency_key="completion:promotion",
                    trace_id=sha256(b"completion:promotion").hexdigest()[:32],
                ),
            )
        )

    service, metadata = _service(
        unit_of_work=postgres_uow,
        database_url=migrated_postgres_database,
        catalog=catalog,
        imports=import_store,
        guest_root=guest_root,
        artifact_root=tmp_path / "artifacts",
        review_root=tmp_path / "reviews",
    )
    try:
        preview = service.create_preview(
            run_id=run.run_id,
            user_id="user_promotion",
            checkpoint_id=checkpoint.checkpoint_id,
            idempotency_key="preview-once",
        )
        assert preview["direct_eligible"] is True
        assert preview["proposed_version"] == "v1.1.0"
        assert preview["changed_files"] == ["main.txt"]
        assert (
            service.create_preview(
                run_id=run.run_id,
                user_id="user_promotion",
                checkpoint_id=checkpoint.checkpoint_id,
                idempotency_key="preview-once",
            )["preview_hash"]
            == preview["preview_hash"]
        )
        with pytest.raises(CommandError, match="promotion_preview_not_found"):
            service.current(run_id=run.run_id, user_id="user_other")

        def interrupt_after_confirmation(**_: object) -> str:
            raise RuntimeError("simulated process loss")

        monkeypatch.setattr(service, "_apply_or_recover", interrupt_after_confirmation)
        with pytest.raises(RuntimeError, match="simulated process loss"):
            _confirm(service, run_id=run.run_id, preview=preview)
    finally:
        metadata.close()

    recovered, recovered_metadata = _service(
        unit_of_work=postgres_uow,
        database_url=migrated_postgres_database,
        catalog=catalog,
        imports=import_store,
        guest_root=guest_root,
        artifact_root=tmp_path / "artifacts",
        review_root=tmp_path / "reviews",
    )
    try:
        result = _confirm(recovered, run_id=run.run_id, preview=preview)
        assert result["status"] == "committed"
        assert result["tag"] == "v1.1.0"
        assert _confirm(recovered, run_id=run.run_id, preview=preview) == result
        with pytest.raises(CommandError, match="promotion_idempotency_conflict"):
            _confirm(
                recovered,
                run_id=run.run_id,
                preview=preview,
                message="Different intent",
            )

        (guest_path / "second.txt").write_text("review only\n")
        fallback_checkpoint = checkpoints.create(
            workspace_id=workspace_id,
            checkpoint_id="checkpoint_user_fallback",
            kind="user_accepted",
            design_version=1,
        )
        (source / "local-only.txt").write_text("do not overwrite\n")
        fallback_preview = recovered.create_preview(
            run_id=run.run_id,
            user_id="user_promotion",
            checkpoint_id=fallback_checkpoint.checkpoint_id,
            idempotency_key="fallback-preview",
        )
        assert fallback_preview["direct_eligible"] is False
        fallback = recovered.confirm(
            run_id=run.run_id,
            user_id="user_promotion",
            preview_hash=str(fallback_preview["preview_hash"]),
            confirm_preview_hash=str(fallback_preview["preview_hash"]),
            confirmation_nonce=str(fallback_preview["confirmation_nonce"]),
            version="v1.2.0",
            message="Preserve this result for review",
            create_tag=False,
            idempotency_key="fallback-confirm",
        )
        assert fallback["status"] == "fallback"
        assert fallback["review_repository_id"]
        assert fallback["review_commit"]
        review = tmp_path / "reviews" / str(fallback["review_repository_id"])
        assert _git(review, "rev-parse", "HEAD") == fallback["review_commit"]
        assert "second.txt" in _git(review, "show", "HEAD:manifest.json")
        assert (
            recovered.confirm(
                run_id=run.run_id,
                user_id="user_promotion",
                preview_hash=str(fallback_preview["preview_hash"]),
                confirm_preview_hash=str(fallback_preview["preview_hash"]),
                confirmation_nonce=str(fallback_preview["confirmation_nonce"]),
                version="v1.2.0",
                message="Preserve this result for review",
                create_tag=False,
                idempotency_key="fallback-confirm",
            )
            == fallback
        )
    finally:
        recovered_metadata.close()

    assert _git(source, "rev-parse", "HEAD") == original_head
    assert _git(source, "status", "--porcelain") == "?? local-only.txt"
    assert (source / "main.txt").read_text() == "base\n"
    assert (source / "local-only.txt").read_text() == "do not overwrite\n"
    branch = str(result["branch"])
    assert _git(source, "show", f"{branch}:main.txt") == "promoted"
    assert _git(source, "rev-list", "-n", "1", "v1.1.0") == result["commit_hash"]
    events = SseEventService(PostgresEventStreamStore(postgres_uow)).replay(
        run_id=run.run_id, user_id="user_promotion", after_sequence=0
    )
    promotion_events = [
        event.event_type
        for event in events
        if event.event_type.startswith("promotion.")
    ]
    assert promotion_events == [
        "promotion.previewed",
        "promotion.confirmed",
        "promotion.committed",
        "promotion.previewed",
        "promotion.rejected",
    ]
