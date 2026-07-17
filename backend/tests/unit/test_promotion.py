"""Isolated host Git promotion fixture tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.checkpoints import CheckpointService, LocalGuestCheckpointAdapter
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion import GitPromotionService
from orchestrator.promotion_preview import (
    LocalGuestPreviewAdapter,
    PreviewCheck,
    PromotionPreviewService,
)
from orchestrator.vm import VmLifecycleService
from orchestrator.workspace import LocalGuestWorkspaceAdapter, WorkspaceImportService


class ReadyAdapter:
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        pass

    def probe_ready(self, guest_id: str) -> bool:
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        pass


def promotion_service(
    tmp_path: Path,
) -> tuple[
    GitPromotionService,
    PromotionPreviewService,
    CheckpointService,
    Path,
    Path,
]:
    root = tmp_path / "sources"
    source = root / "project"
    source.mkdir(parents=True)
    (source / "main.txt").write_text("base")
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
    catalog = ProjectCatalog((root,))
    preview = catalog.discover()[0]
    lifecycle = VmLifecycleService(ReadyAdapter())
    lifecycle.create("run_example")
    guest = tmp_path / "guest"
    imports = WorkspaceImportService(
        catalog, lifecycle, LocalGuestWorkspaceAdapter(guest)
    )
    imported = imports.import_snapshot(
        workspace_id="workspace_example",
        run_id="run_example",
        project_id=preview.project_id,
        expected_source_fingerprint=preview.source_fingerprint,
    )
    checkpoints = CheckpointService(imports, LocalGuestCheckpointAdapter(guest))
    previews = PromotionPreviewService(
        imports, checkpoints, LocalGuestPreviewAdapter(guest)
    )
    service = GitPromotionService(
        previews, imports, catalog, guest, tmp_path / "reviews"
    )
    return (
        service,
        previews,
        checkpoints,
        guest / imported.guest_id / imported.guest_path,
        source,
    )


def test_promotion_uses_isolated_worktree_and_is_idempotent(tmp_path: Path) -> None:
    service, previews, checkpoints, workspace, source = promotion_service(tmp_path)
    before_head = subprocess.check_output(
        ("git", "-C", source, "rev-parse", "HEAD"), text=True
    ).strip()
    (workspace / "main.txt").write_text("promoted")
    checkpoint = checkpoints.create(
        workspace_id="workspace_example",
        checkpoint_id="checkpoint_ready",
        kind="service_accepted",
        design_version=1,
        work_node_id="wn_example",
    )
    preview = previews.preview(
        workspace_id="workspace_example",
        checkpoint_id=checkpoint.checkpoint_id,
        checks=(PreviewCheck("tests", True, "ok"),),
        unresolved_issues=0,
    )
    grant = service.issue_confirmation(
        preview_hash=preview.preview_hash, user_id="user_example"
    )
    result = service.confirm(
        nonce=grant.nonce,
        user_id="user_example",
        idempotency_key="promotion-1",
        version="v1.0.0",
        message="Promote",
        tag="v1.0.0",
    )
    assert result.status == "committed"
    assert (
        subprocess.check_output(
            ("git", "-C", source, "rev-parse", "HEAD"), text=True
        ).strip()
        == before_head
    )
    assert (source / "main.txt").read_text() == "base"
    assert (
        service.confirm(
            nonce="unused",
            user_id="user_example",
            idempotency_key="promotion-1",
            version="v1.0.0",
            message="ignored",
        )
        == result
    )


def test_ineligible_preview_falls_back_without_source_mutation(tmp_path: Path) -> None:
    service, previews, checkpoints, workspace, source = promotion_service(tmp_path)
    (workspace / "main.txt").write_text("change")
    checkpoint = checkpoints.create(
        workspace_id="workspace_example",
        checkpoint_id="checkpoint_failed",
        kind="user_accepted",
        design_version=1,
    )
    preview = previews.preview(
        workspace_id="workspace_example",
        checkpoint_id=checkpoint.checkpoint_id,
        checks=(PreviewCheck("tests", False, "failed"),),
        unresolved_issues=0,
    )
    grant = service.issue_confirmation(
        preview_hash=preview.preview_hash, user_id="user_example"
    )
    result = service.confirm(
        nonce=grant.nonce,
        user_id="user_example",
        idempotency_key="promotion-fallback",
        version="v1.0.0",
        message="Promote",
    )
    assert result.status == "fallback"
    assert (source / "main.txt").read_text() == "base"
