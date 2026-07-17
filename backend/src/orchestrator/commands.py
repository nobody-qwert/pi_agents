"""Prompt-returning command intents; execution is owned by a separate runner."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from orchestrator.model_gateway import ModelGateway
from orchestrator.projects import ProjectCatalog, ProjectPolicyError


class CommandError(Exception):
    """A command could not be recorded safely."""


@dataclass(frozen=True, slots=True)
class RunCommand:
    run_id: str
    user_id: str
    project_id: str
    source_fingerprint: str
    message: str
    status: Literal["queued", "cancelled"]
    idempotency_key: str


class RunCommandService:
    """Records idempotent run/cancel intent without invoking models or tools."""

    def __init__(self, projects: ProjectCatalog, gateway: ModelGateway) -> None:
        self._projects = projects
        self._gateway = gateway
        self._runs: dict[str, RunCommand] = {}
        self._idempotency: dict[tuple[str, str], str] = {}

    def create(
        self, *, user_id: str, project_id: str, message: str, idempotency_key: str
    ) -> RunCommand:
        if not message.strip() or len(message) > 16_384 or not idempotency_key:
            raise CommandError("invalid_run_command")
        key = (user_id, idempotency_key)
        if run_id := self._idempotency.get(key):
            existing = self._runs[run_id]
            if existing.project_id == project_id and existing.message == message:
                return existing
            raise CommandError("idempotency_conflict")
        if self._gateway.readiness().status != "ready":
            raise CommandError("model_not_ready")
        try:
            preview = self._projects.preview(project_id)
        except ProjectPolicyError as error:
            raise CommandError(str(error)) from error
        run = RunCommand(
            run_id="run_" + secrets.token_urlsafe(18),
            user_id=user_id,
            project_id=project_id,
            source_fingerprint=preview.source_fingerprint,
            message=message,
            status="queued",
            idempotency_key=idempotency_key,
        )
        self._runs[run.run_id] = run
        self._idempotency[key] = run.run_id
        return run

    def get(self, *, run_id: str, user_id: str) -> RunCommand:
        run = self._runs.get(run_id)
        if run is None or run.user_id != user_id:
            raise CommandError("run_not_found")
        return run

    def list(self, *, user_id: str) -> tuple[RunCommand, ...]:
        return tuple(
            sorted(
                (run for run in self._runs.values() if run.user_id == user_id),
                key=lambda run: run.run_id,
            )
        )

    def cancel(self, *, run_id: str, user_id: str, idempotency_key: str) -> RunCommand:
        run = self.get(run_id=run_id, user_id=user_id)
        if run.status == "cancelled":
            return run
        if not idempotency_key:
            raise CommandError("missing_idempotency_key")
        cancelled = RunCommand(
            run_id=run.run_id,
            user_id=run.user_id,
            project_id=run.project_id,
            source_fingerprint=run.source_fingerprint,
            message=run.message,
            status="cancelled",
            idempotency_key=run.idempotency_key,
        )
        self._runs[run_id] = cancelled
        return cancelled
