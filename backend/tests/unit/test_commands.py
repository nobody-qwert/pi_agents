"""Prompt-returning run command service tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from orchestrator.commands import CommandError, RunCommandService
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.projects import ProjectCatalog


class Gateway:
    def __init__(
        self,
        status: Literal[
            "ready", "unreachable", "malformed", "model_unavailable"
        ] = "ready",
    ) -> None:
        self.status = status

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status=self.status, configured_model_id="qwen3.6-27b")

    def complete(
        self, request: ModelRequest, *, cancellation: CancellationToken | None = None
    ) -> ModelResponse:
        raise AssertionError


def service(
    tmp_path: Path,
    status: Literal["ready", "unreachable", "malformed", "model_unavailable"] = "ready",
) -> tuple[RunCommandService, str]:
    root = tmp_path / "projects"
    project = root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("example")
    catalog = ProjectCatalog((root,))
    preview = catalog.discover()[0]
    return RunCommandService(catalog, Gateway(status)), preview.project_id


def test_run_commands_are_idempotent_owned_and_prompt_returning(tmp_path: Path) -> None:
    commands, project_id = service(tmp_path)
    first = commands.create(
        user_id="user_example",
        project_id=project_id,
        message="Build it",
        idempotency_key="create-1",
    )
    assert (
        commands.create(
            user_id="user_example",
            project_id=project_id,
            message="Build it",
            idempotency_key="create-1",
        )
        == first
    )
    assert (
        commands.cancel(
            run_id=first.run_id, user_id="user_example", idempotency_key="cancel-1"
        ).status
        == "cancelled"
    )
    with pytest.raises(CommandError, match="run_not_found"):
        commands.get(run_id=first.run_id, user_id="user_other")


def test_run_creation_requires_ready_model(tmp_path: Path) -> None:
    commands, project_id = service(tmp_path, "model_unavailable")
    with pytest.raises(CommandError, match="model_not_ready"):
        commands.create(
            user_id="user_example",
            project_id=project_id,
            message="Build it",
            idempotency_key="create-1",
        )
