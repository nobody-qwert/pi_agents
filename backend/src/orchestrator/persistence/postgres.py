"""PostgreSQL implementations of the authoritative repository ports."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from orchestrator.domain.authoritative import (
    ApprovalRecord,
    ArtifactRecord,
    AuthoritativeRecord,
    CharterRecord,
    CheckpointRecord,
    DesignRevision,
    EvidenceRecord,
    IssueRecord,
    PacketRecord,
    PromotionRecord,
    RunCompletionRecord,
    RunRecord,
    TransitionRecord,
    WorkNodeRecord,
    WorkspaceRecord,
)
from orchestrator.domain.events import EventDetail, EventDraft, EventEnvelope
from orchestrator.domain.primitives import IdempotencyKey, RecordVersion
from orchestrator.persistence.ports import (
    AuthoritativeRepository,
    ConcurrentWriteError,
    DuplicateIdempotencyKeyError,
    DuplicateRecordError,
    RepositoryConstraintError,
)


@dataclass(frozen=True)
class _RepositoryDefinition[RecordT: AuthoritativeRecord]:
    table_name: str
    id_column: str
    model_type: type[RecordT]
    projection_columns: tuple[str, ...] = ()


class PostgresAuthoritativeRepository[RecordT: AuthoritativeRecord]:
    """Maps one domain aggregate to its PostgreSQL owner table."""

    def __init__(
        self,
        connection: Connection,
        definition: _RepositoryDefinition[RecordT],
    ) -> None:
        self._connection = connection
        self._definition = definition

    def add(self, record: RecordT) -> None:
        values = _record_values(record, self._definition)
        columns = ", ".join(values)
        parameters = ", ".join(
            "CAST(:payload AS jsonb)" if name == "payload" else f":{name}"
            for name in values
        )
        statement = text(
            f"INSERT INTO {self._definition.table_name} ({columns}) "
            f"VALUES ({parameters})"
        )
        try:
            with self._connection.begin_nested():
                self._connection.execute(statement, values)
        except IntegrityError as error:
            if _is_duplicate_identifier(error, self._definition.table_name):
                raise DuplicateRecordError(
                    f"duplicate {self._definition.table_name} identifier "
                    f"{values[self._definition.id_column]!r}"
                ) from error
            if _is_duplicate_idempotency_key(error, self._definition.table_name):
                raise DuplicateIdempotencyKeyError(
                    f"duplicate {self._definition.table_name} idempotency key "
                    f"{values['idempotency_key']!r}"
                ) from error
            raise RepositoryConstraintError(
                f"{self._definition.table_name} rejected authoritative record "
                f"{values[self._definition.id_column]!r}"
            ) from error

    def get(self, record_id: str) -> RecordT | None:
        statement = text(
            f"SELECT payload FROM {self._definition.table_name} "
            f"WHERE {self._definition.id_column} = :record_id"
        )
        payload = self._connection.execute(statement, {"record_id": record_id}).scalar()
        if payload is None:
            return None
        return _decode_stored_record(self._definition.model_type, payload)

    def get_by_idempotency_key(self, idempotency_key: IdempotencyKey) -> RecordT | None:
        statement = text(
            f"SELECT payload FROM {self._definition.table_name} "
            "WHERE idempotency_key = :idempotency_key"
        )
        payload = self._connection.execute(
            statement, {"idempotency_key": idempotency_key}
        ).scalar()
        if payload is None:
            return None
        return _decode_stored_record(self._definition.model_type, payload)

    def compare_and_swap(
        self, record: RecordT, *, expected_record_version: RecordVersion
    ) -> None:
        if record.metadata.record_version != expected_record_version + 1:
            raise ConcurrentWriteError(
                "replacement record_version must be exactly one greater than "
                f"expected version {expected_record_version}"
            )
        values = _record_values(record, self._definition)
        values["expected_record_version"] = expected_record_version
        projection_updates = ", ".join(
            f"{column} = :{column}" for column in self._definition.projection_columns
        )
        projection_updates = f", {projection_updates}" if projection_updates else ""
        statement = text(
            f"UPDATE {self._definition.table_name} "
            "SET record_version = :record_version, updated_at = :updated_at, "
            "idempotency_key = :idempotency_key, trace_id = :trace_id, "
            f"payload = CAST(:payload AS jsonb){projection_updates} "
            f"WHERE {self._definition.id_column} = :{self._definition.id_column} "
            "AND record_version = :expected_record_version "
            "AND :record_version = :expected_record_version + 1"
        )
        try:
            with self._connection.begin_nested():
                result = self._connection.execute(statement, values)
        except IntegrityError as error:
            if _is_duplicate_idempotency_key(error, self._definition.table_name):
                raise DuplicateIdempotencyKeyError(
                    f"duplicate {self._definition.table_name} idempotency key "
                    f"{values['idempotency_key']!r}"
                ) from error
            raise RepositoryConstraintError(
                f"{self._definition.table_name} rejected authoritative record "
                f"{values[self._definition.id_column]!r}"
            ) from error
        if result.rowcount != 1:
            raise ConcurrentWriteError(
                f"stale or missing {self._definition.table_name} identifier "
                f"{values[self._definition.id_column]!r}"
            )


class EventNotFoundError(Exception):
    """A durable event or its owning run does not exist."""


class EventConflictError(Exception):
    """An event identifier conflicts with a different originating command."""


class PostgresRunEventRepository:
    """Transaction-bound storage for ordered audit event projections."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_command_idempotency_key(
        self, *, run_id: str, command_idempotency_key: IdempotencyKey
    ) -> EventEnvelope | None:
        payload = self._connection.execute(
            text(
                "SELECT payload FROM run_events "
                "WHERE run_id = :run_id "
                "AND command_idempotency_key = :command_idempotency_key"
            ),
            {
                "run_id": run_id,
                "command_idempotency_key": command_idempotency_key,
            },
        ).scalar()
        return _decode_event_envelope(payload) if payload is not None else None

    def reserve_command(
        self, *, run_id: str, command_idempotency_key: IdempotencyKey
    ) -> bool:
        """Atomically reserve a command before its state transition runs.

        PostgreSQL waits for a concurrent insert of the same unique key to
        finish.  Thus exactly one transaction receives ``True`` and may run
        the authoritative state change; a retry receives ``False`` only after
        the winning transaction has committed its event.  Different command
        keys do not contend here, including keys for the same run.
        """
        reserved_run_id = self._connection.execute(
            text(
                "INSERT INTO run_event_commands "
                "(run_id, command_idempotency_key) "
                "VALUES (:run_id, :command_idempotency_key) "
                "ON CONFLICT DO NOTHING "
                "RETURNING run_id"
            ),
            {
                "run_id": run_id,
                "command_idempotency_key": command_idempotency_key,
            },
        ).scalar()
        return reserved_run_id is not None

    def append(self, draft: EventDraft) -> EventEnvelope:
        """Persist one event with a locked, monotonically allocated sequence."""
        existing = self.get_by_command_idempotency_key(
            run_id=draft.run_id,
            command_idempotency_key=draft.command_idempotency_key,
        )
        if existing is not None:
            return existing

        sequence = self._connection.execute(
            text(
                "WITH stamp AS (SELECT clock_timestamp() AS value) "
                "UPDATE runs "
                "SET next_event_sequence = next_event_sequence + 1, "
                "record_version = record_version + 1, "
                "updated_at = stamp.value, "
                "payload = jsonb_set("
                "payload, '{metadata}', "
                "COALESCE(payload -> 'metadata', '{}'::jsonb) "
                "|| jsonb_build_object("
                "'record_version', record_version + 1, "
                "'updated_at', stamp.value"
                "), true) "
                "FROM stamp "
                "WHERE run_id = :run_id "
                "RETURNING next_event_sequence - 1"
            ),
            {"run_id": draft.run_id},
        ).scalar()
        if sequence is None:
            raise EventNotFoundError(f"run {draft.run_id!r} does not exist")

        envelope = draft.envelope(cast(int, sequence))
        values = {
            "event_id": envelope.event_id,
            "run_id": envelope.run_id,
            "sequence": envelope.sequence,
            "event_type": envelope.type,
            "occurred_at": envelope.occurred_at,
            "attempt_id": envelope.attempt_id,
            "design_version": envelope.design_version,
            "packet_version": envelope.packet_version,
            "actor_role": envelope.actor_role,
            "outcome": envelope.outcome,
            "correlation_id": envelope.correlation_id,
            "trace_id": envelope.trace_id,
            "span_id": envelope.span_id,
            "payload": json.dumps(
                envelope.model_dump(mode="json"), separators=(",", ":")
            ),
            "command_idempotency_key": draft.command_idempotency_key,
            "transition_id": draft.transition_id,
            "inline_detail": (
                json.dumps(draft.inline_detail, separators=(",", ":"))
                if draft.inline_detail is not None
                else None
            ),
            "detail_ref": envelope.detail_ref,
        }
        try:
            with self._connection.begin_nested():
                self._connection.execute(
                    text(
                        "INSERT INTO run_events ("
                        "event_id, run_id, sequence, event_type, occurred_at, "
                        "attempt_id, design_version, packet_version, actor_role, outcome, "
                        "correlation_id, trace_id, span_id, payload, "
                        "command_idempotency_key, transition_id, inline_detail, detail_ref"
                        ") VALUES ("
                        ":event_id, :run_id, :sequence, :event_type, :occurred_at, "
                        ":attempt_id, :design_version, :packet_version, :actor_role, "
                        ":outcome, :correlation_id, :trace_id, :span_id, CAST(:payload AS jsonb), "
                        ":command_idempotency_key, "
                        ":transition_id, CAST(:inline_detail AS jsonb), :detail_ref"
                        ")"
                    ),
                    values,
                )
        except IntegrityError as error:
            existing = self.get_by_command_idempotency_key(
                run_id=draft.run_id,
                command_idempotency_key=draft.command_idempotency_key,
            )
            if existing is not None:
                return existing
            raise EventConflictError(
                f"event {draft.event_id!r} conflicts with a different command"
            ) from error
        return envelope

    def replay(self, *, run_id: str, after_sequence: int) -> tuple[EventEnvelope, ...]:
        rows = self._connection.execute(
            text(
                "SELECT payload FROM run_events "
                "WHERE run_id = :run_id AND sequence > :after_sequence "
                "ORDER BY sequence ASC, event_id ASC"
            ),
            {"run_id": run_id, "after_sequence": after_sequence},
        )
        return tuple(_decode_event_envelope(row[0]) for row in rows)

    def detail(self, *, event_id: str) -> EventDetail:
        row = self._connection.execute(
            text(
                "SELECT detail_ref, inline_detail FROM run_events "
                "WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        ).one_or_none()
        if row is None:
            raise EventNotFoundError(f"event {event_id!r} does not exist")
        return EventDetail.model_validate_json(
            json.dumps(
                {
                    "event_id": event_id,
                    "detail_ref": row.detail_ref,
                    "inline_detail": row.inline_detail,
                },
                separators=(",", ":"),
            )
        )


class PostgresUnitOfWork:
    """Creates repository adapters bound to one explicit PostgreSQL transaction."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._connection: Connection | None = None
        self._repositories: dict[str, PostgresAuthoritativeRepository[Any]] = {}

    @contextmanager
    def transaction(self) -> Iterator[PostgresUnitOfWork]:
        if self._connection is not None:
            yield self
            return

        with self._engine.begin() as connection:
            self._connection = connection
            self._install_repositories()
            try:
                yield self
            finally:
                self._connection = None
                self._repositories = {}

    def close(self) -> None:
        """Release the adapter's connection pool."""
        self._engine.dispose()

    def iter_repositories(
        self,
    ) -> Iterator[AuthoritativeRepository[AuthoritativeRecord]]:
        yield from cast(
            tuple[AuthoritativeRepository[AuthoritativeRecord], ...],
            tuple(self._repositories.values()),
        )

    def _install_repositories(self) -> None:
        connection = self._require_connection()
        self._repositories = {
            attribute: PostgresAuthoritativeRepository(connection, definition)
            for attribute, definition in _REPOSITORIES.items()
        }

    @property
    def runs(self) -> PostgresAuthoritativeRepository[RunRecord]:
        return self._repository("runs", RunRecord)

    @property
    def charters(self) -> PostgresAuthoritativeRepository[CharterRecord]:
        return self._repository("charters", CharterRecord)

    @property
    def design_revisions(self) -> PostgresAuthoritativeRepository[DesignRevision]:
        return self._repository("design_revisions", DesignRevision)

    @property
    def work_nodes(self) -> PostgresAuthoritativeRepository[WorkNodeRecord]:
        return self._repository("work_nodes", WorkNodeRecord)

    @property
    def packets(self) -> PostgresAuthoritativeRepository[PacketRecord]:
        return self._repository("packets", PacketRecord)

    @property
    def artifacts(self) -> PostgresAuthoritativeRepository[ArtifactRecord]:
        return self._repository("artifacts", ArtifactRecord)

    @property
    def evidence(self) -> PostgresAuthoritativeRepository[EvidenceRecord]:
        return self._repository("evidence", EvidenceRecord)

    @property
    def issues(self) -> PostgresAuthoritativeRepository[IssueRecord]:
        return self._repository("issues", IssueRecord)

    @property
    def approvals(self) -> PostgresAuthoritativeRepository[ApprovalRecord]:
        return self._repository("approvals", ApprovalRecord)

    @property
    def workspace_sessions(self) -> PostgresAuthoritativeRepository[WorkspaceRecord]:
        return self._repository("workspace_sessions", WorkspaceRecord)

    @property
    def workspace_checkpoints(
        self,
    ) -> PostgresAuthoritativeRepository[CheckpointRecord]:
        return self._repository("workspace_checkpoints", CheckpointRecord)

    @property
    def promotions(self) -> PostgresAuthoritativeRepository[PromotionRecord]:
        return self._repository("promotions", PromotionRecord)

    @property
    def transition_log(self) -> PostgresAuthoritativeRepository[TransitionRecord]:
        return self._repository("transition_log", TransitionRecord)

    @property
    def run_completions(self) -> PostgresAuthoritativeRepository[RunCompletionRecord]:
        return self._repository("run_completions", RunCompletionRecord)

    @property
    def events(self) -> PostgresRunEventRepository:
        """Expose event operations only while the caller owns the transaction."""
        return PostgresRunEventRepository(self._require_connection())

    def _repository[RecordT: AuthoritativeRecord](
        self, attribute: str, model_type: type[RecordT]
    ) -> PostgresAuthoritativeRepository[RecordT]:
        try:
            repository = self._repositories[attribute]
        except KeyError as error:
            raise RuntimeError(
                "repositories may only be used inside transaction()"
            ) from error
        if repository._definition.model_type is not model_type:
            raise RuntimeError(f"repository {attribute!r} has an unexpected model type")
        return cast(PostgresAuthoritativeRepository[RecordT], repository)

    def _require_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("repositories may only be used inside transaction()")
        return self._connection


def _record_values[RecordT: AuthoritativeRecord](
    record: RecordT,
    definition: _RepositoryDefinition[RecordT],
) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    metadata = record.metadata
    record_id = cast(str, payload[definition.id_column])
    values: dict[str, Any] = {
        definition.id_column: record_id,
        "run_id": payload["run_id"],
        "record_version": metadata.record_version,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
        "idempotency_key": metadata.idempotency_key,
        "trace_id": metadata.trace_id,
        "payload": json.dumps(payload, separators=(",", ":")),
    }
    values.update({column: payload[column] for column in definition.projection_columns})
    return values


def _decode_stored_record[RecordT: AuthoritativeRecord](
    model_type: type[RecordT], payload: Any
) -> RecordT:
    """Restore JSON-native values before strict validation of database payloads.

    PostgreSQL JSONB values are returned as Python containers, whose timestamp
    values are ISO strings. Re-encoding them and using Pydantic's JSON entry
    point permits JSON's datetime representation only for this persistence
    round trip; external callers still use strict Python-mode validation.
    """
    serialized_payload = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    return model_type.model_validate_json(serialized_payload)


def _decode_event_envelope(payload: Any) -> EventEnvelope:
    serialized_payload = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    return EventEnvelope.model_validate_json(serialized_payload)


def _is_duplicate_identifier(error: IntegrityError, table_name: str) -> bool:
    original_error = error.orig
    diagnostic = getattr(original_error, "diag", None)
    return (
        getattr(original_error, "sqlstate", None) == "23505"
        and getattr(diagnostic, "constraint_name", None) == f"{table_name}_pkey"
    )


def _is_duplicate_idempotency_key(error: IntegrityError, table_name: str) -> bool:
    original_error = error.orig
    diagnostic = getattr(original_error, "diag", None)
    return (
        getattr(original_error, "sqlstate", None) == "23505"
        and getattr(diagnostic, "constraint_name", None)
        == f"uq_{table_name}_idempotency_key"
    )


_REPOSITORIES: dict[str, _RepositoryDefinition[Any]] = {
    "runs": _RepositoryDefinition("runs", "run_id", RunRecord, ("tenant_id",)),
    "charters": _RepositoryDefinition("charters", "charter_id", CharterRecord),
    "design_revisions": _RepositoryDefinition(
        "design_revisions", "design_revision_id", DesignRevision, ("design_version",)
    ),
    "work_nodes": _RepositoryDefinition(
        "work_nodes", "work_node_id", WorkNodeRecord, ("parent_id",)
    ),
    "packets": _RepositoryDefinition(
        "packets", "packet_id", PacketRecord, ("work_node_id",)
    ),
    "artifacts": _RepositoryDefinition(
        "artifacts", "artifact_id", ArtifactRecord, ("work_node_id",)
    ),
    "evidence": _RepositoryDefinition(
        "evidence", "evidence_id", EvidenceRecord, ("work_node_id",)
    ),
    "issues": _RepositoryDefinition("issues", "issue_id", IssueRecord),
    "approvals": _RepositoryDefinition("approvals", "approval_id", ApprovalRecord),
    "workspace_sessions": _RepositoryDefinition(
        "workspace_sessions", "workspace_id", WorkspaceRecord
    ),
    "workspace_checkpoints": _RepositoryDefinition(
        "workspace_checkpoints",
        "checkpoint_id",
        CheckpointRecord,
        ("workspace_id", "work_node_id", "parent_checkpoint_id"),
    ),
    "promotions": _RepositoryDefinition(
        "promotions", "promotion_id", PromotionRecord, ("workspace_id",)
    ),
    "transition_log": _RepositoryDefinition(
        "transition_log", "transition_id", TransitionRecord, ("work_node_id",)
    ),
    "run_completions": _RepositoryDefinition(
        "run_completions", "completion_id", RunCompletionRecord
    ),
}
