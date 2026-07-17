"""FastAPI factory for safe versioned read-only control-plane queries."""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from orchestrator.commands import CommandError, RunCommandService
from orchestrator.graph import compile_control_graph, project_registry
from orchestrator.graph.registry import AgentRegistry
from orchestrator.model_gateway import ModelGateway
from orchestrator.projects import ProjectCatalog, ProjectPolicyError
from orchestrator.sse import EventStreamError, SseEventService


class ApiError(BaseModel):
    code: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ApiServices:
    registry: AgentRegistry
    projects: ProjectCatalog
    gateway: ModelGateway
    commands: RunCommandService | None = None
    events: SseEventService | None = None


class RunCreateRequest(BaseModel):
    project_id: str
    message: str


class RunCommandResponse(BaseModel):
    run_id: str
    project_id: str
    status: str


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

    @app.exception_handler(CommandError)
    async def command_error(request: Request, error: CommandError) -> JSONResponse:
        status = 409 if str(error) == "idempotency_conflict" else 400
        return _error(request, status, str(error))

    @app.exception_handler(EventStreamError)
    async def stream_error(request: Request, error: EventStreamError) -> JSONResponse:
        return _error(request, 400, str(error))

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

    @app.post("/api/v1/runs", response_model=RunCommandResponse, status_code=202)
    def create_run(
        body: RunCreateRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> RunCommandResponse:
        commands = _commands(services)
        run = commands.create(
            user_id=user_id,
            project_id=body.project_id,
            message=body.message,
            idempotency_key=idempotency_key or "",
        )
        return RunCommandResponse(
            run_id=run.run_id, project_id=run.project_id, status=run.status
        )

    @app.get("/api/v1/runs", response_model=list[RunCommandResponse])
    def list_runs(
        user_id: Annotated[str, Depends(_identity)],
    ) -> list[RunCommandResponse]:
        return [
            RunCommandResponse(
                run_id=run.run_id, project_id=run.project_id, status=run.status
            )
            for run in _commands(services).list(user_id=user_id)
        ]

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunCommandResponse)
    def cancel_run(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> RunCommandResponse:
        run = _commands(services).cancel(
            run_id=run_id, user_id=user_id, idempotency_key=idempotency_key or ""
        )
        return RunCommandResponse(
            run_id=run.run_id, project_id=run.project_id, status=run.status
        )

    @app.get("/api/v1/runs/{run_id}/events")
    def events(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        try:
            cursor = int(last_event_id) if last_event_id is not None else 0
        except ValueError as error:
            raise EventStreamError("invalid_event_cursor") from error
        service = _events(services)
        payload = service.encode(
            service.replay(run_id=run_id, user_id=user_id, after_sequence=cursor)
        )
        return StreamingResponse(iter((payload,)), media_type="text/event-stream")

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


def _commands(services: ApiServices) -> RunCommandService:
    if services.commands is None:
        raise HTTPException(status_code=503, detail="commands_unavailable")
    return services.commands


def _events(services: ApiServices) -> SseEventService:
    if services.events is None:
        raise HTTPException(status_code=503, detail="events_unavailable")
    return services.events
