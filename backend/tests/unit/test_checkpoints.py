"""Guest checkpoint lineage and rollback fixture tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.checkpoints import (
    CheckpointError,
    CheckpointService,
    LocalGuestCheckpointAdapter,
)
from orchestrator.projects import ProjectCatalog
from orchestrator.vm import VmLifecycleService
from orchestrator.workspace import LocalGuestWorkspaceAdapter, WorkspaceImportService


class ReadyAdapter:
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        pass

    def probe_ready(self, guest_id: str) -> bool:
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        pass


def checkpoint_service(tmp_path: Path) -> tuple[CheckpointService, Path]:
    source_root = tmp_path / "sources"
    source = source_root / "project"
    source.mkdir(parents=True)
    (source / "main.txt").write_text("baseline")
    catalog = ProjectCatalog((source_root,))
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
    return CheckpointService(imports, LocalGuestCheckpointAdapter(guest_root)), (
        guest_root / imported.guest_id / imported.guest_path
    )


def test_checkpoint_lineage_and_idempotent_rollback(tmp_path: Path) -> None:
    service, workspace = checkpoint_service(tmp_path)
    (workspace / "main.txt").write_text("accepted")
    accepted = service.create(
        workspace_id="workspace_example",
        checkpoint_id="checkpoint_accepted",
        kind="service_accepted",
        design_version=1,
        work_node_id="wn_example",
        evidence_ids=("evidence_example",),
    )
    (workspace / "main.txt").write_text("later mutation")
    rollback = service.rollback(
        workspace_id="workspace_example",
        target_checkpoint_id=accepted.checkpoint_id,
        rollback_checkpoint_id="checkpoint_rollback",
        design_version=1,
    )

    assert (workspace / "main.txt").read_text() == "accepted"
    assert rollback.rollback_from_checkpoint_id == accepted.checkpoint_id
    assert (
        service.rollback(
            workspace_id="workspace_example",
            target_checkpoint_id=accepted.checkpoint_id,
            rollback_checkpoint_id="checkpoint_rollback",
            design_version=1,
        )
        == rollback
    )
    assert [
        record.kind for record in service.list_checkpoints("workspace_example")
    ] == [
        "baseline",
        "service_accepted",
        "rollback",
    ]


def test_checkpoint_rejects_foreign_and_incomplete_requests(tmp_path: Path) -> None:
    service, _ = checkpoint_service(tmp_path)
    with pytest.raises(CheckpointError, match="service_checkpoint_requires_work_node"):
        service.create(
            workspace_id="workspace_example",
            checkpoint_id="checkpoint_invalid",
            kind="service_accepted",
            design_version=1,
        )
    with pytest.raises(CheckpointError, match="unknown_checkpoint"):
        service.rollback(
            workspace_id="workspace_example",
            target_checkpoint_id="checkpoint_foreign",
            rollback_checkpoint_id="checkpoint_rollback",
            design_version=1,
        )
