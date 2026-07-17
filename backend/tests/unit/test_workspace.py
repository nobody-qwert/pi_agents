"""Sanitized guest workspace import tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.projects import ProjectCatalog
from orchestrator.vm import VmLifecycleService
from orchestrator.workspace import (
    LocalGuestWorkspaceAdapter,
    WorkspaceImportError,
    WorkspaceImportService,
)


class ReadyAdapter:
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        pass

    def probe_ready(self, guest_id: str) -> bool:
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        pass


def service_for(
    source_root: Path, guest_root: Path
) -> tuple[WorkspaceImportService, ProjectCatalog]:
    catalog = ProjectCatalog((source_root,))
    lifecycle = VmLifecycleService(ReadyAdapter())
    lifecycle.create("run_example")
    return WorkspaceImportService(
        catalog, lifecycle, LocalGuestWorkspaceAdapter(guest_root)
    ), catalog


def test_import_excludes_host_metadata_and_creates_separate_guest_baseline(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    source = source_root / "project"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('source')")
    (source / ".env").write_text("host secret")
    service, catalog = service_for(source_root, tmp_path / "guest")
    preview = catalog.discover()[0]

    result = service.import_snapshot(
        workspace_id="workspace_example",
        run_id="run_example",
        project_id=preview.project_id,
        expected_source_fingerprint=preview.source_fingerprint,
    )
    destination = tmp_path / "guest" / result.guest_id / result.guest_path

    assert (destination / "main.py").read_text() == "print('source')"
    assert not (destination / ".env").exists()
    assert (destination / ".git").is_dir()
    assert len(result.baseline.commit_hash) == 40
    assert len(result.manifest) == 1
    (destination / "main.py").write_text("guest-only mutation")
    assert (source / "main.py").read_text() == "print('source')"
    assert catalog.discover()[0].source_fingerprint == preview.source_fingerprint


def test_import_rejects_a_changed_source_before_guest_writes(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source = source_root / "project"
    source.mkdir(parents=True)
    (source / "main.py").write_text("before")
    service, catalog = service_for(source_root, tmp_path / "guest")
    preview = catalog.discover()[0]
    (source / "main.py").write_text("after")

    with pytest.raises(WorkspaceImportError, match="source_fingerprint_changed"):
        service.import_snapshot(
            workspace_id="workspace_example",
            run_id="run_example",
            project_id=preview.project_id,
            expected_source_fingerprint=preview.source_fingerprint,
        )
    assert not (tmp_path / "guest").exists()
