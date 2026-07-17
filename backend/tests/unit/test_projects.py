"""Allowlisted project catalog tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.projects import ProjectCatalog, ProjectPolicyError


def test_catalog_uses_opaque_ids_and_excludes_secrets_build_output_and_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowlisted"
    source = root / "project"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('ok')")
    (source / ".env").write_text("secret")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "package.js").write_text("not imported")
    (source / "private.key").write_text("not imported")
    (source / "linked").symlink_to(tmp_path)
    catalog = ProjectCatalog((root,))

    preview = catalog.discover()[0]

    assert preview.project_id.startswith("project_")
    assert preview.file_count == 1
    assert preview.excluded_paths == (".env", "linked", "node_modules", "private.key")
    assert catalog.resolve(preview.project_id) == source.resolve()
    with pytest.raises(ProjectPolicyError, match="unknown_project_id"):
        catalog.resolve("/etc/passwd")


def test_catalog_reports_git_head_and_dirty_state_without_exposing_git_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowlisted"
    source = root / "project"
    source.mkdir(parents=True)
    (source / "README.md").write_text("baseline")
    subprocess.run(("git", "init", "--quiet", source), check=True)
    subprocess.run(("git", "-C", source, "add", "README.md"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            source,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ),
        check=True,
    )
    catalog = ProjectCatalog((root,))

    clean = catalog.discover()[0]
    (source / "README.md").write_text("changed")
    dirty = catalog.discover()[0]

    assert clean.git_head is not None
    assert clean.git_dirty is False
    assert dirty.git_head == clean.git_head
    assert dirty.git_dirty is True
    assert ".git" in dirty.excluded_paths
    assert dirty.protected_paths == ("README.md",)


def test_catalog_rejects_nested_and_symlinked_roots_and_projects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowlisted"
    nested = root / "nested"
    root.mkdir()
    nested.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match="nested"):
        ProjectCatalog((root, nested))
    with pytest.raises(ValueError, match="symlink"):
        ProjectCatalog((link,))

    (root / "linked-project").symlink_to(nested, target_is_directory=True)
    assert [preview.display_name for preview in ProjectCatalog((root,)).discover()] == [
        "nested"
    ]


def test_catalog_detects_a_file_changed_while_it_is_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "allowlisted"
    source = root / "project"
    source.mkdir(parents=True)
    target = source / "main.py"
    target.write_text("before")
    catalog = ProjectCatalog((root,))
    original_fstat = os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        result = original_fstat(descriptor)
        if calls == 2:
            values = list(result)
            values[6] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(os, "fstat", changed_fstat)
    with pytest.raises(ProjectPolicyError, match="project_changed_during_inspection"):
        catalog.discover()
