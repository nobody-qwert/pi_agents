"""Immutable guest promotion preview tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.checkpoints import CheckpointService, LocalGuestCheckpointAdapter
from orchestrator.projects import ProjectCatalog
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


def preview_service(
    tmp_path: Path,
) -> tuple[PromotionPreviewService, CheckpointService, Path]:
    root = tmp_path / "sources"
    source = root / "project"
    source.mkdir(parents=True)
    (source / "main.txt").write_text("base")
    (source / "README.md").write_text("protected baseline")
    subprocess.run(("git", "init", "--quiet", source), check=True)
    subprocess.run(("git", "-C", source, "add", "main.txt", "README.md"), check=True)
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
    guest_root = tmp_path / "guest"
    imports = WorkspaceImportService(
        catalog, lifecycle, LocalGuestWorkspaceAdapter(guest_root)
    )
    imported = imports.import_snapshot(
        workspace_id="workspace_example",
        run_id="run_example",
        project_id=preview.project_id,
        expected_source_fingerprint=preview.source_fingerprint,
    )
    checkpoints = CheckpointService(imports, LocalGuestCheckpointAdapter(guest_root))
    service = PromotionPreviewService(
        imports, checkpoints, LocalGuestPreviewAdapter(guest_root)
    )
    return service, checkpoints, guest_root / imported.guest_id / imported.guest_path


def test_identical_preview_is_immutable_and_proposes_next_minor(tmp_path: Path) -> None:
    service, checkpoints, workspace = preview_service(tmp_path)
    (workspace / "main.txt").write_text("changed")
    checkpoint = checkpoints.create(
        workspace_id="workspace_example",
        checkpoint_id="checkpoint_accepted",
        kind="service_accepted",
        design_version=1,
        work_node_id="wn_example",
    )
    first = service.preview(
        workspace_id="workspace_example",
        checkpoint_id=checkpoint.checkpoint_id,
        checks=(PreviewCheck("tests", True, "ok"),),
        unresolved_issues=0,
        source_tags=("v1.2.3",),
    )
    second = service.preview(
        workspace_id="workspace_example",
        checkpoint_id=checkpoint.checkpoint_id,
        checks=(PreviewCheck("tests", True, "ok"),),
        unresolved_issues=0,
        source_tags=("v1.2.3",),
    )
    assert first == second
    assert first.direct_eligible is True
    assert first.proposed_version == "v1.3.0"
    assert first.changed_paths[0].path == "main.txt"


def test_failed_checks_or_protected_paths_disable_direct_promotion(
    tmp_path: Path,
) -> None:
    service, checkpoints, workspace = preview_service(tmp_path)
    (workspace / "README.md").write_text("protected")
    checkpoint = checkpoints.create(
        workspace_id="workspace_example",
        checkpoint_id="checkpoint_protected",
        kind="user_accepted",
        design_version=1,
    )
    preview = service.preview(
        workspace_id="workspace_example",
        checkpoint_id=checkpoint.checkpoint_id,
        checks=(PreviewCheck("tests", False, "failed"),),
        unresolved_issues=1,
    )
    assert preview.direct_eligible is False
    assert set(preview.ineligible_reasons) == {
        "required_checks_failed",
        "unresolved_issues",
        "protected_path_changed",
    }
