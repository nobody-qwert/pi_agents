"""Restart-durable storage for untrusted model invocation attempts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import RowMapping

from orchestrator.graph.registry import AgentRegistry
from orchestrator.invocation import InvocationInput, InvocationResult
from orchestrator.persistence import PostgresUnitOfWork


class AttemptConflictError(RuntimeError):
    """An attempt identity is already bound to different immutable input."""


@dataclass(frozen=True, slots=True)
class StoredAttempt:
    attempt_id: str
    run_id: str
    agent_id: str
    status: Literal["started", "accepted", "rejected"]
    result_type: str | None
    result_payload: dict[str, object] | None
    rejection_code: str | None
    retryable: bool | None


class PostgresAgentAttemptStore:
    """Persists registry provenance, bounded input, output, and rejections."""

    def __init__(
        self, unit_of_work: PostgresUnitOfWork, registry: AgentRegistry
    ) -> None:
        self._unit_of_work = unit_of_work
        self._registry = registry
        self._registry_version_id = f"registry_{registry.registry_hash}"

    def begin(
        self,
        invocation: InvocationInput,
        *,
        input_context: dict[str, object],
        trace_id: str,
        now: datetime | None = None,
    ) -> StoredAttempt:
        timestamp = now or datetime.now(UTC)
        definition = self._registry[invocation.agent_id]
        context_json = _json(input_context)
        with self._unit_of_work.transaction() as unit_of_work:
            connection = unit_of_work.connection
            connection.execute(
                text(
                    "INSERT INTO agent_registry_versions "
                    "(registry_version_id, config_hash, snapshot, created_at) "
                    "VALUES (:registry_version_id, :config_hash, CAST(:snapshot AS jsonb), :created_at) "
                    "ON CONFLICT (config_hash) DO NOTHING"
                ),
                {
                    "registry_version_id": self._registry_version_id,
                    "config_hash": self._registry.registry_hash,
                    "snapshot": _json(self._snapshot()),
                    "created_at": timestamp,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_attempts ("
                    "attempt_id, run_id, registry_version_id, status, usage, trace_id, "
                    "created_at, agent_id, work_node_id, design_version, config_hash, "
                    "prompt_hash, input_context"
                    ") VALUES ("
                    ":attempt_id, :run_id, :registry_version_id, 'started', '{}'::jsonb, "
                    ":trace_id, :created_at, :agent_id, :work_node_id, :design_version, "
                    ":config_hash, :prompt_hash, CAST(:input_context AS jsonb)"
                    ") ON CONFLICT (attempt_id) DO NOTHING"
                ),
                {
                    "attempt_id": invocation.attempt_id,
                    "run_id": invocation.run_id,
                    "registry_version_id": self._registry_version_id,
                    "trace_id": trace_id,
                    "created_at": timestamp,
                    "agent_id": invocation.agent_id,
                    "work_node_id": invocation.work_node_id,
                    "design_version": invocation.design_version,
                    "config_hash": definition.config_hash,
                    "prompt_hash": definition.prompt_hash,
                    "input_context": context_json,
                },
            )
            row = connection.execute(
                text(
                    "SELECT attempt_id, run_id, agent_id, status, result_type, "
                    "result_payload, rejection_code, retryable, design_version, "
                    "work_node_id, config_hash, prompt_hash, input_context "
                    "FROM agent_attempts WHERE attempt_id = :attempt_id"
                ),
                {"attempt_id": invocation.attempt_id},
            ).mappings().one()
            if (
                row["run_id"] != invocation.run_id
                or row["agent_id"] != invocation.agent_id
                or row["design_version"] != invocation.design_version
                or row["work_node_id"] != invocation.work_node_id
                or row["config_hash"] != definition.config_hash
                or row["prompt_hash"] != definition.prompt_hash
                or row["input_context"] != json.loads(context_json)
            ):
                raise AttemptConflictError("attempt_identity_conflict")
        return _stored(row)

    def accept(
        self,
        attempt_id: str,
        result: InvocationResult,
        *,
        now: datetime | None = None,
    ) -> StoredAttempt:
        timestamp = now or datetime.now(UTC)
        result_type = type(result.result).__name__
        payload = result.result.model_dump(mode="json")
        usage = {
            "finish_reason": result.finish_reason,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
        }
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "UPDATE agent_attempts SET status = 'accepted', completed_at = :now, "
                    "model_id = :model_id, result_type = :result_type, "
                    "result_payload = CAST(:result_payload AS jsonb), "
                    "usage = CAST(:usage AS jsonb), retryable = false "
                    "WHERE attempt_id = :attempt_id AND status = 'started' "
                    "RETURNING attempt_id, run_id, agent_id, status, result_type, "
                    "result_payload, rejection_code, retryable"
                ),
                {
                    "attempt_id": attempt_id,
                    "now": timestamp,
                    "model_id": result.model_id,
                    "result_type": result_type,
                    "result_payload": _json(payload),
                    "usage": _json(usage),
                },
            ).mappings().one_or_none()
            if row is None:
                row = self._get_row(unit_of_work, attempt_id)
                if (
                    row["status"] != "accepted"
                    or row["result_type"] != result_type
                    or row["result_payload"] != payload
                ):
                    raise AttemptConflictError("attempt_completion_conflict")
        return _stored(row)

    def reject(
        self,
        attempt_id: str,
        *,
        code: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> StoredAttempt:
        timestamp = now or datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "UPDATE agent_attempts SET status = 'rejected', completed_at = :now, "
                    "rejection_code = :code, retryable = :retryable "
                    "WHERE attempt_id = :attempt_id AND status = 'started' "
                    "RETURNING attempt_id, run_id, agent_id, status, result_type, "
                    "result_payload, rejection_code, retryable"
                ),
                {
                    "attempt_id": attempt_id,
                    "now": timestamp,
                    "code": code,
                    "retryable": retryable,
                },
            ).mappings().one_or_none()
            if row is None:
                row = self._get_row(unit_of_work, attempt_id)
                if (
                    row["status"] != "rejected"
                    or row["rejection_code"] != code
                    or row["retryable"] != retryable
                ):
                    raise AttemptConflictError("attempt_rejection_conflict")
        return _stored(row)

    def latest_accepted(
        self, *, run_id: str, agent_id: str, result_type: str
    ) -> StoredAttempt | None:
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "SELECT attempt_id, run_id, agent_id, status, result_type, "
                    "result_payload, rejection_code, retryable FROM agent_attempts "
                    "WHERE run_id = :run_id AND agent_id = :agent_id "
                    "AND result_type = :result_type AND status = 'accepted' "
                    "ORDER BY created_at DESC, attempt_id DESC LIMIT 1"
                ),
                {
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "result_type": result_type,
                },
            ).mappings().one_or_none()
        return _stored(row) if row is not None else None

    @staticmethod
    def _get_row(
        unit_of_work: PostgresUnitOfWork, attempt_id: str
    ) -> RowMapping:
        row = unit_of_work.connection.execute(
            text(
                "SELECT attempt_id, run_id, agent_id, status, result_type, "
                "result_payload, rejection_code, retryable FROM agent_attempts "
                "WHERE attempt_id = :attempt_id"
            ),
            {"attempt_id": attempt_id},
        ).mappings().one_or_none()
        if row is None:
            raise LookupError("attempt_not_found")
        return row

    def _snapshot(self) -> dict[str, object]:
        return {
            "registry_hash": self._registry.registry_hash,
            "agents": {
                agent_id: {
                    "config": definition.config.model_dump(mode="json"),
                    "config_hash": definition.config_hash,
                    "prompt": definition.prompt,
                    "prompt_hash": definition.prompt_hash,
                }
                for agent_id, definition in self._registry.definitions.items()
            },
        }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stored(values: RowMapping) -> StoredAttempt:
    return StoredAttempt(
        attempt_id=values["attempt_id"],
        run_id=values["run_id"],
        agent_id=values["agent_id"],
        status=values["status"],
        result_type=values["result_type"],
        result_payload=values["result_payload"],
        rejection_code=values["rejection_code"],
        retryable=values["retryable"],
    )
