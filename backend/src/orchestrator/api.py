"""FastAPI factory for safe versioned read-only control-plane queries."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from typing import Annotated, Literal, Protocol, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from orchestrator.artifact_api import ArtifactContent
from orchestrator.checkpoints import CheckpointError
from orchestrator.commands import CommandError, RunCommand, RunCommands
from orchestrator.graph import compile_control_graph, project_registry
from orchestrator.graph.registry import AgentRegistry
from orchestrator.model_gateway import ModelGateway
from orchestrator.projects import ProjectCatalog, ProjectPolicyError
from orchestrator.sse import EventStreamError, SseEventService
from orchestrator.telemetry import SafeTelemetry
from orchestrator.vm import VmLifecycleError
from orchestrator.workspace import WorkspaceImportError
from orchestrator.workspace_api import WorkspacePreviewResponse


class ApiError(BaseModel):
    code: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ApiServices:
    registry: AgentRegistry
    projects: ProjectCatalog
    gateway: ModelGateway
    commands: RunCommands | None = None
    events: SseEventService | None = None
    database_ready: Callable[[], bool] | None = None
    workspace: WorkspaceApi | None = None
    run_queries: RunQueries | None = None
    telemetry: SafeTelemetry | None = None
    approvals: ApprovalApi | None = None
    desktop: DesktopApi | None = None
    promotions: PromotionApi | None = None
    conversations: ConversationApi | None = None
    artifact_reader: ArtifactReadApi | None = None


class ConversationApi(Protocol):
    def create(self, *, user_id: str, idempotency_key: str) -> dict[str, object]: ...

    def list(self, *, user_id: str) -> dict[str, object]: ...

    def get(self, *, conversation_id: str, user_id: str) -> dict[str, object]: ...

    def add_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        content: str,
        project_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class ArtifactReadApi(Protocol):
    def read(self, *, artifact_id: str, user_id: str) -> ArtifactContent: ...


class ApprovalApi(Protocol):
    def decide(
        self,
        *,
        run_id: str,
        approval_id: str,
        user_id: str,
        decision: Literal["approved", "rejected"],
        comment: str | None,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class DesktopApi(Protocol):
    def issue_session(
        self, *, run_id: str, user_id: str, idempotency_key: str
    ) -> dict[str, object]: ...

    def change_owner(
        self,
        *,
        run_id: str,
        user_id: str,
        requested_owner: Literal["AGENT", "USER"],
        idempotency_key: str,
    ) -> dict[str, object]: ...


class PromotionApi(Protocol):
    def create_preview(
        self,
        *,
        run_id: str,
        user_id: str,
        checkpoint_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]: ...

    def current(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def list_promotions(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def confirm(
        self,
        *,
        run_id: str,
        user_id: str,
        preview_hash: str,
        confirm_preview_hash: str,
        confirmation_nonce: str,
        version: str,
        message: str,
        create_tag: bool,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class WorkspaceApi(Protocol):
    def prepare(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def get(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def list_checkpoints(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def list_previews(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def rollback_preview(
        self, *, run_id: str, user_id: str, target_checkpoint_id: str
    ) -> dict[str, object]: ...

    def fetch_preview(self, *, token: str, target: str) -> WorkspacePreviewResponse: ...

    def create_checkpoint(
        self, *, run_id: str, user_id: str, idempotency_key: str
    ) -> dict[str, object]: ...

    def rollback(
        self,
        *,
        run_id: str,
        user_id: str,
        target_checkpoint_id: str,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class RunQueries(Protocol):
    def conversation(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def work_graph(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def artifacts(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def approvals(self, *, run_id: str, user_id: str) -> dict[str, object]: ...


class RunCreateRequest(BaseModel):
    project_id: str
    message: str


class RollbackRequest(BaseModel):
    confirm: bool


class ApprovalDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None


class InputOwnerRequest(BaseModel):
    owner: str


class PromotionPreviewRequest(BaseModel):
    checkpoint_id: str | None = None


class PromotionRequest(BaseModel):
    preview_hash: str
    confirm_preview_hash: str
    confirmation_nonce: str
    version: str
    message: str
    tag: bool = False


class ConversationMessageRequest(BaseModel):
    content: str
    project_id: str | None = None


class RunCommandResponse(BaseModel):
    run_id: str
    project_id: str
    status: str
    conversation_id: str | None = None
    message: str
    current_gate: str
    source_fingerprint: str
    created_at: str | None = None
    updated_at: str | None = None


def _run_response(command: RunCommand) -> RunCommandResponse:
    return RunCommandResponse(
        run_id=command.run_id,
        project_id=command.project_id,
        status=command.status,
        conversation_id=command.conversation_id,
        message=command.message,
        current_gate=command.current_gate,
        source_fingerprint=command.source_fingerprint,
        created_at=(
            command.created_at.isoformat() if command.created_at is not None else None
        ),
        updated_at=(
            command.updated_at.isoformat() if command.updated_at is not None else None
        ),
    )


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(title="Deterministic Agent Orchestrator", version="v1")

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        identifier = request.headers.get("X-Request-ID") or secrets.token_hex(12)
        request.state.request_id = identifier
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = identifier
        if services.telemetry is not None:
            duration_ms = int((time.monotonic() - started) * 1000)
            services.telemetry.span(
                "api.request",
                operation=f"http.{request.method.lower()}",
                status=str(response.status_code),
                duration_ms=duration_ms,
            )
            services.telemetry.metric(
                "orchestrator.api.request.duration",
                float(duration_ms),
                operation=f"http.{request.method.lower()}",
                status=str(response.status_code),
            )
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
        status = {
            "idempotency_conflict": 409,
            "run_not_found": 404,
            "conversation_not_found": 404,
            "artifact_not_found": 404,
            "approval_expired": 409,
            "approval_stale": 409,
            "model_not_ready": 503,
        }.get(str(error), 400)
        return _error(request, status, str(error))

    @app.exception_handler(EventStreamError)
    async def stream_error(request: Request, error: EventStreamError) -> JSONResponse:
        return _error(request, 400, str(error))

    @app.exception_handler(CheckpointError)
    async def checkpoint_error(
        request: Request, error: CheckpointError
    ) -> JSONResponse:
        return _error(request, 409, str(error))

    @app.exception_handler(VmLifecycleError)
    async def vm_error(request: Request, error: VmLifecycleError) -> JSONResponse:
        return _error(request, 409, str(error))

    @app.exception_handler(WorkspaceImportError)
    async def workspace_error(
        request: Request, error: WorkspaceImportError
    ) -> JSONResponse:
        return _error(request, 409, str(error))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error(request, error.status_code, str(error.detail))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        if services.database_ready is not None and not services.database_ready():
            raise HTTPException(status_code=503, detail="database_unavailable")
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

    @app.post("/api/v1/conversations", status_code=201)
    def create_conversation(
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _conversations(services).create(
            user_id=user_id, idempotency_key=idempotency_key or ""
        )

    @app.get("/api/v1/conversations")
    def list_conversations(
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _conversations(services).list(user_id=user_id)

    @app.get("/api/v1/conversations/{conversation_id}")
    def get_conversation(
        conversation_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _conversations(services).get(
            conversation_id=conversation_id, user_id=user_id
        )

    @app.post("/api/v1/conversations/{conversation_id}/messages", status_code=202)
    def add_conversation_message(
        conversation_id: str,
        body: ConversationMessageRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _conversations(services).add_message(
            conversation_id=conversation_id,
            user_id=user_id,
            content=body.content,
            project_id=body.project_id,
            idempotency_key=idempotency_key or "",
        )

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
        return _run_response(run)

    @app.get("/api/v1/runs", response_model=list[RunCommandResponse])
    def list_runs(
        user_id: Annotated[str, Depends(_identity)],
    ) -> list[RunCommandResponse]:
        return [_run_response(run) for run in _commands(services).list(user_id=user_id)]

    @app.get("/api/v1/runs/{run_id}", response_model=RunCommandResponse)
    def get_run(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> RunCommandResponse:
        run = _commands(services).get(run_id=run_id, user_id=user_id)
        return _run_response(run)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunCommandResponse)
    def cancel_run(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> RunCommandResponse:
        run = _commands(services).cancel(
            run_id=run_id, user_id=user_id, idempotency_key=idempotency_key or ""
        )
        return _run_response(run)

    @app.get("/api/v1/runs/{run_id}/events")
    async def events(
        request: Request,
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        try:
            cursor = int(last_event_id) if last_event_id is not None else 0
        except ValueError as error:
            raise EventStreamError("invalid_event_cursor") from error
        service = _events(services)
        initial = service.replay(run_id=run_id, user_id=user_id, after_sequence=cursor)

        async def stream() -> AsyncIterator[str]:
            current = cursor
            pending = initial
            heartbeat_ticks = 0
            while True:
                if pending:
                    for event in pending:
                        yield service.encode((event,))
                        current = event.sequence
                        if event.terminal:
                            return
                    heartbeat_ticks = 0
                elif heartbeat_ticks == 0:
                    yield service.encode(())
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.5)
                heartbeat_ticks = (heartbeat_ticks + 1) % 20
                pending = await asyncio.to_thread(
                    service.replay,
                    run_id=run_id,
                    user_id=user_id,
                    after_sequence=current,
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/runs/{run_id}/conversation")
    def conversation(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _run_queries(services).conversation(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/work-graph")
    def work_graph(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _run_queries(services).work_graph(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/artifacts")
    def artifacts(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _run_queries(services).artifacts(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/artifacts/{artifact_id}", response_model=None)
    def artifact(
        artifact_id: str,
        user_id: Annotated[str, Depends(_identity)],
        download: bool = False,
    ) -> dict[str, object] | Response:
        result = _artifact_reader(services).read(
            artifact_id=artifact_id, user_id=user_id
        )
        if not download:
            return result.projection()
        return Response(
            content=result.content,
            media_type=result.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{artifact_id}.bin"',
                "X-Content-Type-Options": "nosniff",
                "ETag": f'"sha256:{result.sha256}"',
            },
        )

    @app.get("/api/v1/runs/{run_id}/approvals")
    def approvals(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _run_queries(services).approvals(run_id=run_id, user_id=user_id)

    @app.post("/api/v1/runs/{run_id}/approvals/{approval_id}/decisions")
    def decide_approval(
        run_id: str,
        approval_id: str,
        body: ApprovalDecisionRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if body.decision not in {"approved", "rejected"}:
            raise HTTPException(status_code=400, detail="invalid_approval_decision")
        return _approvals(services).decide(
            run_id=run_id,
            approval_id=approval_id,
            user_id=user_id,
            decision=cast(Literal["approved", "rejected"], body.decision),
            comment=body.comment,
            idempotency_key=idempotency_key or "",
        )

    @app.get("/api/v1/runs/{run_id}/events/{event_id}/detail")
    def event_detail(
        run_id: str,
        event_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _events(services).detail(
            run_id=run_id, event_id=event_id, user_id=user_id
        )

    @app.post("/api/v1/runs/{run_id}/workspace", status_code=202)
    def prepare_workspace(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _workspace(services).prepare(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/workspace")
    def workspace(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _workspace(services).get(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/workspace/checkpoints")
    def checkpoints(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _workspace(services).list_checkpoints(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/workspace/previews")
    def workspace_previews(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _workspace(services).list_previews(run_id=run_id, user_id=user_id)

    @app.get(
        "/api/v1/runs/{run_id}/workspace/checkpoints/{checkpoint_id}/rollback-preview"
    )
    def checkpoint_rollback_preview(
        run_id: str,
        checkpoint_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _workspace(services).rollback_preview(
            run_id=run_id,
            user_id=user_id,
            target_checkpoint_id=checkpoint_id,
        )

    def preview_response(token: str, preview_path: str, request: Request) -> Response:
        target = "/" + preview_path
        if request.url.query:
            target += "?" + request.url.query
        result = _workspace(services).fetch_preview(token=token, target=target)
        return Response(
            content=result.content,
            status_code=result.status_code,
            media_type=result.content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": (
                    "sandbox allow-forms allow-modals allow-popups allow-scripts "
                    "allow-same-origin"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/workspace-previews/{token}/")
    def workspace_preview_root(token: str, request: Request) -> Response:
        return preview_response(token, "", request)

    @app.get("/api/v1/workspace-previews/{token}/{preview_path:path}")
    def workspace_preview_path(
        token: str, preview_path: str, request: Request
    ) -> Response:
        return preview_response(token, preview_path, request)

    @app.post("/api/v1/runs/{run_id}/workspace/checkpoints")
    def create_checkpoint(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _workspace(services).create_checkpoint(
            run_id=run_id,
            user_id=user_id,
            idempotency_key=idempotency_key or "",
        )

    @app.post("/api/v1/runs/{run_id}/workspace/desktop-sessions")
    def desktop_session(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _desktop(services).issue_session(
            run_id=run_id,
            user_id=user_id,
            idempotency_key=idempotency_key or "",
        )

    @app.post("/api/v1/runs/{run_id}/workspace/input-owner")
    def change_input_owner(
        run_id: str,
        body: InputOwnerRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if body.owner not in {"AGENT", "USER"}:
            raise HTTPException(status_code=400, detail="invalid_input_owner")
        return _desktop(services).change_owner(
            run_id=run_id,
            user_id=user_id,
            requested_owner=cast(Literal["AGENT", "USER"], body.owner),
            idempotency_key=idempotency_key or "",
        )

    @app.post("/api/v1/runs/{run_id}/workspace/checkpoints/{checkpoint_id}/rollback")
    def rollback(
        run_id: str,
        checkpoint_id: str,
        body: RollbackRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if not body.confirm:
            raise HTTPException(status_code=400, detail="rollback_not_confirmed")
        return _workspace(services).rollback(
            run_id=run_id,
            user_id=user_id,
            target_checkpoint_id=checkpoint_id,
            idempotency_key=idempotency_key or "",
        )

    @app.get("/api/v1/runs/{run_id}/promotions")
    def promotions(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _promotions(services).list_promotions(run_id=run_id, user_id=user_id)

    @app.get("/api/v1/runs/{run_id}/promotions/current")
    def current_promotion_preview(
        run_id: str,
        user_id: Annotated[str, Depends(_identity)],
    ) -> dict[str, object]:
        return _promotions(services).current(run_id=run_id, user_id=user_id)

    @app.post("/api/v1/runs/{run_id}/promotion-previews")
    def create_promotion_preview(
        run_id: str,
        body: PromotionPreviewRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _promotions(services).create_preview(
            run_id=run_id,
            user_id=user_id,
            checkpoint_id=body.checkpoint_id,
            idempotency_key=idempotency_key or "",
        )

    @app.post("/api/v1/runs/{run_id}/promotions")
    def confirm_promotion(
        run_id: str,
        body: PromotionRequest,
        user_id: Annotated[str, Depends(_identity)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        return _promotions(services).confirm(
            run_id=run_id,
            user_id=user_id,
            preview_hash=body.preview_hash,
            confirm_preview_hash=body.confirm_preview_hash,
            confirmation_nonce=body.confirmation_nonce,
            version=body.version,
            message=body.message,
            create_tag=body.tag,
            idempotency_key=idempotency_key or "",
        )

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


def _commands(services: ApiServices) -> RunCommands:
    if services.commands is None:
        raise HTTPException(status_code=503, detail="commands_unavailable")
    return services.commands


def _events(services: ApiServices) -> SseEventService:
    if services.events is None:
        raise HTTPException(status_code=503, detail="events_unavailable")
    return services.events


def _approvals(services: ApiServices) -> ApprovalApi:
    if services.approvals is None:
        raise HTTPException(status_code=503, detail="approvals_unavailable")
    return services.approvals


def _workspace(services: ApiServices) -> WorkspaceApi:
    if services.workspace is None:
        raise HTTPException(status_code=503, detail="workspace_unavailable")
    return services.workspace


def _desktop(services: ApiServices) -> DesktopApi:
    if services.desktop is None:
        raise HTTPException(status_code=503, detail="desktop_unavailable")
    return services.desktop


def _promotions(services: ApiServices) -> PromotionApi:
    if services.promotions is None:
        raise HTTPException(status_code=503, detail="promotions_unavailable")
    return services.promotions


def _run_queries(services: ApiServices) -> RunQueries:
    if services.run_queries is None:
        raise HTTPException(status_code=503, detail="run_queries_unavailable")
    return services.run_queries


def _conversations(services: ApiServices) -> ConversationApi:
    if services.conversations is None:
        raise HTTPException(status_code=503, detail="conversations_unavailable")
    return services.conversations


def _artifact_reader(services: ApiServices) -> ArtifactReadApi:
    if services.artifact_reader is None:
        raise HTTPException(status_code=503, detail="artifacts_unavailable")
    return services.artifact_reader
