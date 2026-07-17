"""Container entrypoint that composes only configured trusted boundaries."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from orchestrator.api import ApiServices, create_app
from orchestrator.commands import RunCommandService
from orchestrator.graph import load_agent_registry
from orchestrator.model_gateway import LmStudioGateway
from orchestrator.projects import ProjectCatalog
from orchestrator.settings import load_settings
from orchestrator.sse import SseEventService, StreamEvent


class _CommandEventStore:
    """Authorize stream access until the durable event repository is composed."""

    def __init__(self, commands: RunCommandService) -> None:
        self._commands = commands

    def replay(
        self, *, run_id: str, user_id: str, after_sequence: int
    ) -> tuple[StreamEvent, ...]:
        del after_sequence
        self._commands.get(run_id=run_id, user_id=user_id)
        return ()


def build_app() -> FastAPI:
    settings = load_settings()
    roots = tuple(
        Path(value).resolve()
        for value in os.environ["PROJECT_ROOTS"].split(":")
        if value
    )
    projects = ProjectCatalog(roots)
    gateway = LmStudioGateway(settings)
    registry = load_agent_registry(
        Path(os.environ.get("ORCHESTRATOR_CONFIG_ROOT", "/app/config"))
    )
    commands = RunCommandService(projects, gateway)
    return create_app(
        ApiServices(
            registry,
            projects,
            gateway,
            commands,
            SseEventService(_CommandEventStore(commands)),
        )
    )


app = build_app()
