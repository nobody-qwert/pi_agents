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
    return create_app(
        ApiServices(registry, projects, gateway, RunCommandService(projects, gateway))
    )


app = build_app()
