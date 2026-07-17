"""Read-only allowlisted project discovery and sanitized-copy preview policy."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ProjectPolicyError(Exception):
    """An untrusted project selection did not resolve inside the catalog."""


_EXCLUDED_NAMES = frozenset({".git", ".env", ".venv", "node_modules", "__pycache__"})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".pem", ".key"})
_PROTECTED_PATHS = frozenset({".env.example", "README.md"})


@dataclass(frozen=True, slots=True)
class ProjectPreview:
    project_id: str
    display_name: str
    source_fingerprint: str
    file_count: int
    included_bytes: int
    excluded_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    git_head: str | None
    git_dirty: bool | None


class ProjectCatalog:
    """Only opaque catalog IDs cross the application/model boundary.

    The catalog is intentionally read-only.  A later trusted import service
    rechecks ``source_fingerprint`` immediately before copying the snapshot.
    """

    def __init__(self, roots: tuple[Path, ...]) -> None:
        if not roots:
            raise ValueError("at least one project root is required")
        resolved_roots = tuple(self._safe_root(root) for root in roots)
        if len(set(resolved_roots)) != len(resolved_roots):
            raise ValueError("project roots must be unique")
        for root in resolved_roots:
            if any(
                root != other and root.is_relative_to(other) for other in resolved_roots
            ):
                raise ValueError("nested project roots are not permitted")
        self._roots = resolved_roots

    def discover(self) -> tuple[ProjectPreview, ...]:
        projects: list[ProjectPreview] = []
        for root in self._roots:
            try:
                candidates = sorted(root.iterdir(), key=lambda path: path.name)
            except OSError as error:
                raise ProjectPolicyError("unreadable_project_root") from error
            for candidate in candidates:
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                projects.append(self._preview_path(candidate))
        return tuple(projects)

    def preview(self, project_id: str) -> ProjectPreview:
        for preview in self.discover():
            if preview.project_id == project_id:
                return preview
        raise ProjectPolicyError("unknown_project_id")

    def resolve(self, project_id: str) -> Path:
        """Resolve only a current direct child selected by an opaque ID."""
        for root in self._roots:
            try:
                candidates = root.iterdir()
                for candidate in candidates:
                    if candidate.is_symlink() or not candidate.is_dir():
                        continue
                    source = self._canonical_project(candidate)
                    if self._project_id(source) == project_id:
                        return source
            except OSError as error:
                raise ProjectPolicyError("unreadable_project_root") from error
        raise ProjectPolicyError("unknown_project_id")

    def _preview_path(self, source: Path) -> ProjectPreview:
        source = self._canonical_project(source)
        included: list[tuple[str, bytes]] = []
        excluded: list[str] = []

        try:
            for directory, dirnames, filenames in os.walk(
                source, topdown=True, followlinks=False
            ):
                directory_path = Path(directory)
                retained_directories: list[str] = []
                for name in sorted(dirnames):
                    path = directory_path / name
                    relative = path.relative_to(source)
                    if path.is_symlink() or self._excluded(relative):
                        excluded.append(relative.as_posix())
                    else:
                        retained_directories.append(name)
                dirnames[:] = retained_directories

                for name in sorted(filenames):
                    path = directory_path / name
                    relative = path.relative_to(source)
                    if path.is_symlink() or self._excluded(relative):
                        excluded.append(relative.as_posix())
                        continue
                    included.append(
                        (relative.as_posix(), self._read_regular_file(path))
                    )
        except OSError as error:
            raise ProjectPolicyError("unreadable_project_path") from error

        # Re-resolving detects replacement of the selected project directory while
        # it was inspected; an importer must never accept such a preview.
        if self._canonical_project(source) != source:
            raise ProjectPolicyError("project_changed_during_inspection")

        fingerprint = hashlib.sha256()
        for relative_path, content in included:
            fingerprint.update(relative_path.encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(hashlib.sha256(content).digest())
        git_head, git_dirty = self._git_info(source)
        return ProjectPreview(
            project_id=self._project_id(source),
            display_name=source.name,
            source_fingerprint=fingerprint.hexdigest(),
            file_count=len(included),
            included_bytes=sum(len(content) for _, content in included),
            excluded_paths=tuple(sorted(excluded)),
            protected_paths=tuple(
                relative for relative, _ in included if relative in _PROTECTED_PATHS
            ),
            git_head=git_head,
            git_dirty=git_dirty,
        )

    def _canonical_project(self, source: Path) -> Path:
        if source.is_symlink():
            raise ProjectPolicyError("symlinked_project")
        try:
            resolved = source.resolve(strict=True)
        except OSError as error:
            raise ProjectPolicyError("unreadable_project_path") from error
        if not resolved.is_dir() or not any(
            resolved.parent == root for root in self._roots
        ):
            raise ProjectPolicyError("source_outside_allowlist")
        return resolved

    @staticmethod
    def _safe_root(root: Path) -> Path:
        if root.is_symlink():
            raise ValueError("project root must not be a symlink")
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise ValueError("project root must be a readable directory") from error
        if not resolved.is_dir():
            raise ValueError("project root must be a real readable directory")
        return resolved

    @staticmethod
    def _excluded(relative: Path) -> bool:
        return (
            any(part in _EXCLUDED_NAMES for part in relative.parts)
            or relative.suffix.lower() in _EXCLUDED_SUFFIXES
        )

    @staticmethod
    def _project_id(source: Path) -> str:
        digest = hashlib.sha256(os.fsencode(source)).hexdigest()[:24]
        return f"project_{digest}"

    @staticmethod
    def _read_regular_file(path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ProjectPolicyError("non_regular_project_path")
            with os.fdopen(descriptor, "rb", closefd=False) as file:
                content = file.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ProjectPolicyError("project_changed_during_inspection")
        return content

    @staticmethod
    def _git_info(source: Path) -> tuple[str | None, bool | None]:
        def git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                ("git", "-C", os.fspath(source), "--no-optional-locks", *arguments),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )

        try:
            inside_work_tree = git("rev-parse", "--is-inside-work-tree")
            if (
                inside_work_tree.returncode != 0
                or inside_work_tree.stdout.strip() != b"true"
            ):
                return None, None
            head = git("rev-parse", "--verify", "HEAD")
            status = git("status", "--porcelain=v1", "--untracked-files=normal")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProjectPolicyError("git_inspection_failed") from error
        if status.returncode != 0:
            raise ProjectPolicyError("git_inspection_failed")
        git_head = head.stdout.decode("ascii").strip() if head.returncode == 0 else None
        return git_head, bool(status.stdout)
