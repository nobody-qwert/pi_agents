"""Prompt-returning command intents; execution is owned by a separate runner."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol

from sqlalchemy import text

from orchestrator.domain import EventDraft, RecordMetadata, RunRecord
from orchestrator.model_gateway import ModelGateway
from orchestrator.persistence import (
    DuplicateIdempotencyKeyError,
    PostgresUnitOfWork,
)
from orchestrator.projects import ProjectCatalog, ProjectPolicyError
from orchestrator.services.events import DurableEventService, EventWakeupNotifier


class CommandError(Exception):
    """A command could not be recorded safely."""


@dataclass(frozen=True, slots=True)
class RunCommand:
    run_id: str
    user_id: str
    project_id: str
    source_fingerprint: str
    message: str
    status: Literal[
        "queued", "running", "paused", "cancelled", "blocked", "completed", "failed"
    ]
    idempotency_key: str
    conversation_id: str | None = None
    current_gate: str = "INTAKE"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunCommands(Protocol):
    """Command/query contract used by HTTP and stream authorization adapters."""

    def create(
        self, *, user_id: str, project_id: str, message: str, idempotency_key: str
    ) -> RunCommand: ...

    def get(self, *, run_id: str, user_id: str) -> RunCommand: ...

    def list(self, *, user_id: str) -> tuple[RunCommand, ...]: ...

    def cancel(
        self, *, run_id: str, user_id: str, idempotency_key: str
    ) -> RunCommand: ...


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
        timestamp = datetime.now(UTC)
        run = RunCommand(
            run_id="run_" + secrets.token_urlsafe(18),
            user_id=user_id,
            project_id=project_id,
            source_fingerprint=preview.source_fingerprint,
            message=message,
            status="queued",
            idempotency_key=idempotency_key,
            created_at=timestamp,
            updated_at=timestamp,
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
            conversation_id=run.conversation_id,
            current_gate=run.current_gate,
            created_at=run.created_at,
            updated_at=datetime.now(UTC),
        )
        self._runs[run_id] = cancelled
        return cancelled


class _NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


class PostgresRunCommandService:
    """Atomically persists owned run intent, queue state, and audit events."""

    def __init__(
        self,
        projects: ProjectCatalog,
        gateway: ModelGateway,
        unit_of_work: PostgresUnitOfWork,
        *,
        notifier: EventWakeupNotifier | None = None,
        now: Callable[[], datetime] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._projects = projects
        self._gateway = gateway
        self._unit_of_work = unit_of_work
        self._events = DurableEventService(unit_of_work, notifier or _NoopNotifier())
        self._now = now or (lambda: datetime.now(UTC))
        self._max_attempts = max_attempts

    def create(
        self, *, user_id: str, project_id: str, message: str, idempotency_key: str
    ) -> RunCommand:
        return self._create(
            user_id=user_id,
            project_id=project_id,
            message=message,
            idempotency_key=idempotency_key,
            conversation_id=None,
        )

    def create_in_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        project_id: str,
        message: str,
        idempotency_key: str,
    ) -> RunCommand:
        return self._create(
            user_id=user_id,
            project_id=project_id,
            message=message,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
        )

    def _create(
        self,
        *,
        user_id: str,
        project_id: str,
        message: str,
        idempotency_key: str,
        conversation_id: str | None,
    ) -> RunCommand:
        message = message.strip()
        self._validate_create(user_id, message, idempotency_key)
        command_key = self._key("create", user_id, idempotency_key)
        existing = self._get_by_command_key(command_key)
        if existing is not None:
            if (
                existing.project_id == project_id
                and existing.message == message
                and (
                    conversation_id is None
                    or existing.conversation_id == conversation_id
                )
            ):
                return existing
            raise CommandError("idempotency_conflict")
        if conversation_id is not None:
            with self._unit_of_work.transaction() as unit_of_work:
                owned = unit_of_work.connection.execute(
                    text(
                        "SELECT conversation_id FROM conversations "
                        "WHERE conversation_id = :conversation_id AND user_id = :user_id"
                    ),
                    {"conversation_id": conversation_id, "user_id": user_id},
                ).scalar()
            if owned is None:
                raise CommandError("conversation_not_found")
        if self._gateway.readiness().status != "ready":
            raise CommandError("model_not_ready")
        try:
            preview = self._projects.preview(project_id)
        except ProjectPolicyError as error:
            raise CommandError(str(error)) from error

        suffix = secrets.token_hex(12)
        run_id = f"run_{suffix}"
        selected_conversation_id = conversation_id or f"conv_{suffix}"
        timestamp = self._now()
        trace_id = secrets.token_hex(16)
        record = RunRecord(
            metadata=RecordMetadata(
                record_version=1,
                created_at=timestamp,
                updated_at=timestamp,
                idempotency_key=command_key,
                trace_id=trace_id,
            ),
            run_id=run_id,
            tenant_id="tenant_local",
            outcome=message,
            current_gate="INTAKE",
            risk_class="low",
            status="created",
        )
        draft = self._draft(
            run_id=run_id,
            conversation_id=selected_conversation_id,
            event_type="run.created",
            status="created",
            summary="Run accepted and queued",
            command_key=command_key,
            trace_id=trace_id,
            timestamp=timestamp,
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            connection = unit_of_work.connection
            connection.execute(
                text(
                    "INSERT INTO users (user_id, tenant_id, display_name, created_at) "
                    "VALUES (:user_id, 'tenant_local', :user_id, :created_at) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"user_id": user_id, "created_at": timestamp},
            )
            if conversation_id is None:
                connection.execute(
                    text(
                        "INSERT INTO conversations "
                        "(conversation_id, user_id, tenant_id, created_at) "
                        "VALUES (:conversation_id, :user_id, 'tenant_local', :created_at)"
                    ),
                    {
                        "conversation_id": selected_conversation_id,
                        "user_id": user_id,
                        "created_at": timestamp,
                    },
                )
                sequence = 1
            else:
                locked = connection.execute(
                    text(
                        "SELECT conversation_id FROM conversations WHERE "
                        "conversation_id = :conversation_id AND user_id = :user_id "
                        "FOR UPDATE"
                    ),
                    {
                        "conversation_id": selected_conversation_id,
                        "user_id": user_id,
                    },
                ).scalar()
                if locked is None:
                    raise CommandError("conversation_not_found")
                sequence = int(
                    connection.execute(
                        text(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages "
                            "WHERE conversation_id = :conversation_id"
                        ),
                        {"conversation_id": selected_conversation_id},
                    ).scalar_one()
                )
            connection.execute(
                text(
                    "INSERT INTO messages "
                    "(message_id, conversation_id, sequence, role, content, created_at) "
                    "VALUES (:message_id, :conversation_id, :sequence, 'user', "
                    ":content, :created_at)"
                ),
                {
                    "message_id": f"msg_{suffix}",
                    "conversation_id": selected_conversation_id,
                    "sequence": sequence,
                    "content": message,
                    "created_at": timestamp,
                },
            )
            unit_of_work.runs.add(record)
            connection.execute(
                text(
                    "UPDATE runs SET user_id = :user_id, conversation_id = :conversation_id, "
                    "project_id = :project_id, source_fingerprint = :source_fingerprint, "
                    "record_version = record_version + 1, updated_at = :now, "
                    "payload = jsonb_set(payload, '{metadata}', "
                    "(payload -> 'metadata') || jsonb_build_object("
                    "'record_version', record_version + 1, 'updated_at', :now), true) "
                    "WHERE run_id = :run_id"
                ),
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "conversation_id": selected_conversation_id,
                    "project_id": project_id,
                    "source_fingerprint": preview.source_fingerprint,
                    "now": timestamp,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO run_queue "
                    "(run_id, available_at, lease_epoch, attempt_count, max_attempts, "
                    "created_at, updated_at) "
                    "VALUES (:run_id, :now, 0, 0, :max_attempts, :now, :now)"
                ),
                {
                    "run_id": run_id,
                    "now": timestamp,
                    "max_attempts": self._max_attempts,
                },
            )

        try:
            self._events.apply(draft, persist)
        except DuplicateIdempotencyKeyError:
            raced = self._get_by_command_key(command_key)
            if (
                raced is not None
                and raced.project_id == project_id
                and raced.message == message
                and (
                    conversation_id is None or raced.conversation_id == conversation_id
                )
            ):
                return raced
            raise CommandError("idempotency_conflict") from None
        return self.get(run_id=run_id, user_id=user_id)

    def get(self, *, run_id: str, user_id: str) -> RunCommand:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        self._select()
                        + " WHERE runs.run_id = :run_id AND runs.user_id = :user_id"
                    ),
                    {"run_id": run_id, "user_id": user_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise CommandError("run_not_found")
        return self._from_row(row)

    def list(self, *, user_id: str) -> tuple[RunCommand, ...]:
        with self._unit_of_work.transaction() as unit_of_work:
            rows = unit_of_work.connection.execute(
                text(
                    self._select()
                    + " WHERE runs.user_id = :user_id ORDER BY runs.created_at DESC"
                ),
                {"user_id": user_id},
            ).mappings()
            return tuple(self._from_row(row) for row in rows)

    def cancel(self, *, run_id: str, user_id: str, idempotency_key: str) -> RunCommand:
        if not idempotency_key or len(idempotency_key) > 256:
            raise CommandError("missing_idempotency_key")
        current = self.get(run_id=run_id, user_id=user_id)
        if current.status in {"completed", "failed", "blocked"}:
            raise CommandError("run_not_cancellable")
        if current.status == "cancelled":
            return current
        command_key = self._key("cancel", user_id, idempotency_key, run_id=run_id)
        timestamp = self._now()
        trace_id = secrets.token_hex(16)
        draft = self._draft(
            run_id=run_id,
            conversation_id=current.conversation_id or f"conv_legacy_{run_id[4:]}",
            event_type="run.cancel_requested",
            status="paused",
            summary="Run cancellation requested",
            command_key=command_key,
            trace_id=trace_id,
            timestamp=timestamp,
        )

        def request_cancellation(unit_of_work: PostgresUnitOfWork) -> None:
            updated = unit_of_work.connection.execute(
                text(
                    "UPDATE run_queue SET cancellation_requested_at = COALESCE("
                    "cancellation_requested_at, :now), updated_at = :now "
                    "WHERE run_id = :run_id AND completed_at IS NULL RETURNING run_id"
                ),
                {"run_id": run_id, "now": timestamp},
            ).scalar()
            if updated is None:
                raise CommandError("run_not_cancellable")

        self._events.apply(draft, request_cancellation)
        return self.get(run_id=run_id, user_id=user_id)

    def _get_by_command_key(self, command_key: str) -> RunCommand | None:
        with self._unit_of_work.transaction() as unit_of_work:
            record = unit_of_work.runs.get_by_idempotency_key(command_key)
            if record is None:
                return None
            row = (
                unit_of_work.connection.execute(
                    text(self._select() + " WHERE runs.run_id = :run_id"),
                    {"run_id": record.run_id},
                )
                .mappings()
                .one()
            )
            return self._from_row(row)

    @staticmethod
    def _select() -> str:
        return (
            "SELECT runs.run_id, runs.user_id, runs.project_id, runs.source_fingerprint, "
            "runs.conversation_id, runs.idempotency_key, runs.payload ->> 'outcome' AS message, "
            "runs.payload ->> 'status' AS run_status, runs.payload ->> 'current_gate' AS current_gate, "
            "runs.created_at, runs.updated_at, queue.cancellation_requested_at "
            "FROM runs LEFT JOIN run_queue AS queue ON queue.run_id = runs.run_id"
        )

    @staticmethod
    def _from_row(row: object) -> RunCommand:
        values = row
        status = values["run_status"]  # type: ignore[index]
        if values["cancellation_requested_at"] is not None:  # type: ignore[index]
            status = "cancelled"
        elif status == "created":
            status = "queued"
        return RunCommand(
            run_id=values["run_id"],  # type: ignore[index]
            user_id=values["user_id"],  # type: ignore[index]
            project_id=values["project_id"],  # type: ignore[index]
            source_fingerprint=values["source_fingerprint"],  # type: ignore[index]
            message=values["message"],  # type: ignore[index]
            status=status,
            idempotency_key=values["idempotency_key"],  # type: ignore[index]
            conversation_id=values["conversation_id"],  # type: ignore[index]
            current_gate=values["current_gate"],  # type: ignore[index]
            created_at=values["created_at"],  # type: ignore[index]
            updated_at=values["updated_at"],  # type: ignore[index]
        )

    @staticmethod
    def _validate_create(user_id: str, message: str, idempotency_key: str) -> None:
        if not user_id.startswith("user_"):
            raise CommandError("invalid_user")
        if (
            not message
            or len(message) > 16_384
            or not idempotency_key
            or len(idempotency_key) > 256
        ):
            raise CommandError("invalid_run_command")

    @staticmethod
    def _key(action: str, user_id: str, key: str, *, run_id: str = "") -> str:
        digest = sha256(f"{action}\0{user_id}\0{run_id}\0{key}".encode()).hexdigest()
        return f"run:{action}:{digest}"

    @staticmethod
    def _draft(
        *,
        run_id: str,
        conversation_id: str,
        event_type: Literal["run.created", "run.cancel_requested"],
        status: Literal["created", "paused"],
        summary: str,
        command_key: str,
        trace_id: str,
        timestamp: datetime,
    ) -> EventDraft:
        suffix = secrets.token_hex(12)
        return EventDraft(
            event_id=f"evt_{suffix}",
            run_id=run_id,
            conversation_id=conversation_id,
            occurred_at=timestamp,
            type=event_type,
            stage="INTAKE",
            node_id="command-api",
            attempt_id=f"attempt_{suffix}",
            design_version=1,
            packet_version=1,
            actor_role="operator",
            status=status,
            outcome=status,
            summary=summary,
            detail_ref=f"/api/v1/runs/{run_id}/events/evt_{suffix}/detail",
            correlation_id=command_key,
            trace_id=trace_id,
            span_id=secrets.token_hex(8),
            command_idempotency_key=command_key,
        )
