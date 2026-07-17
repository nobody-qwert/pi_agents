"""FastAPI factory for safe versioned read-only control-plane queries."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from orchestrator.graph import compile_control_graph, project_registry
from orchestrator.graph.registry import AgentRegistry
from orchestrator.model_gateway import ModelGateway
from orchestrator.projects import ProjectCatalog, ProjectPolicyError


class ApiError(BaseModel):
    code: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ApiServices:
    registry: AgentRegistry
    projects: ProjectCatalog
    gateway: ModelGateway


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(title="Deterministic Agent Orchestrator", version="v1")

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        identifier = request.headers.get("X-Request-ID") or secrets.token_hex(12)
        request.state.request_id = identifier
        response = await call_next(request)
        response.headers["X-Request-ID"] = identifier
        return response

    @app.exception_handler(ProjectPolicyError)
    async def project_error(
        request: Request, error: ProjectPolicyError
    ) -> JSONResponse:
        return _error(
            request, 404 if str(error) == "unknown_project_id" else 400, str(error)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error(request, error.status_code, str(error.detail))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        readiness = services.gateway.readiness()
        if readiness.status != "ready":
            raise HTTPException(status_code=503, detail=f"model_{readiness.status}")
        return {"status": "ready", "model_id": readiness.configured_model_id}

    @app.get("/api/v1/system/graph")
    def graph(_: Annotated[str, Depends(_identity)]) -> dict[str, object]:
        return compile_control_graph(services.registry).projection.model_dump(
            mode="json"
        )

    @app.get("/api/v1/system/agents")
    def agents(_: Annotated[str, Depends(_identity)]) -> dict[str, object]:
        return project_registry(services.registry).model_dump(mode="json")

    @app.get("/api/v1/projects")
    def projects(_: Annotated[str, Depends(_identity)]) -> dict[str, object]:
        return {
            "projects": [asdict(preview) for preview in services.projects.discover()]
        }

    @app.get("/api/v1/projects/{project_id}")
    def project(
        project_id: str, _: Annotated[str, Depends(_identity)]
    ) -> dict[str, object]:
        if not project_id.startswith("project_"):
            raise HTTPException(status_code=400, detail="invalid_project_id")
        return asdict(services.projects.preview(project_id))

    return app


def _identity(x_dev_user: Annotated[str | None, Header()] = None) -> str:
    if x_dev_user is None or not x_dev_user.startswith("user_"):
        raise HTTPException(status_code=401, detail="unauthorized")
    return x_dev_user


def _error(request: Request, status: int, code: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status,
        content=ApiError(code=code, request_id=request_id).model_dump(),
    )
