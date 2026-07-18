"""Owned durable conversation, work-graph, artifact, and approval projections."""

from __future__ import annotations

from typing import cast

from sqlalchemy import text

from orchestrator.commands import CommandError
from orchestrator.persistence import PostgresUnitOfWork


class PostgresRunQueryService:
    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def conversation(self, *, run_id: str, user_id: str) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            conversation_id = self._conversation_id(unit_of_work, run_id, user_id)
            rows = unit_of_work.connection.execute(
                text(
                    "SELECT message_id, sequence, role, content, created_at FROM messages "
                    "WHERE conversation_id = :conversation_id ORDER BY sequence"
                ),
                {"conversation_id": conversation_id},
            ).mappings()
            return {
                "conversation_id": conversation_id,
                "messages": [
                    {
                        "message_id": row["message_id"],
                        "sequence": row["sequence"],
                        "role": row["role"],
                        "content": row["content"],
                        "created_at": row["created_at"].isoformat(),
                    }
                    for row in rows
                ],
            }

    def work_graph(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._require_owned(run_id, user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            nodes = unit_of_work.connection.execute(
                text(
                    "SELECT payload FROM work_nodes WHERE run_id = :run_id "
                    "ORDER BY work_node_id"
                ),
                {"run_id": run_id},
            ).scalars()
            edges = unit_of_work.connection.execute(
                text(
                    "SELECT edge_id, from_work_node_id, to_work_node_id, edge_type "
                    "FROM work_edges WHERE run_id = :run_id ORDER BY edge_id"
                ),
                {"run_id": run_id},
            ).mappings()
            return {
                "nodes": [cast(dict[str, object], value) for value in nodes],
                "edges": [dict(row) for row in edges],
            }

    def artifacts(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._require_owned(run_id, user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            values = unit_of_work.connection.execute(
                text(
                    "SELECT payload FROM artifacts WHERE run_id = :run_id "
                    "ORDER BY artifact_id"
                ),
                {"run_id": run_id},
            ).scalars()
            return {"artifacts": [cast(dict[str, object], value) for value in values]}

    def approvals(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._require_owned(run_id, user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            rows = unit_of_work.connection.execute(
                text(
                    "SELECT approval_id, authority, affected_versions, status, comment, "
                    "expires_at "
                    "FROM approval_requests WHERE run_id = :run_id "
                    "ORDER BY requested_at, approval_id"
                ),
                {"run_id": run_id},
            ).mappings()
            return {
                "approvals": [
                    {
                        "approval_id": row["approval_id"],
                        "authority": row["authority"],
                        "affected_versions": list(row["affected_versions"]),
                        "expires_at": row["expires_at"].isoformat(),
                        "status": row["status"],
                        "comment": row["comment"],
                    }
                    for row in rows
                ]
            }

    def _require_owned(self, run_id: str, user_id: str) -> None:
        with self._unit_of_work.transaction() as unit_of_work:
            self._conversation_id(unit_of_work, run_id, user_id)

    @staticmethod
    def _conversation_id(
        unit_of_work: PostgresUnitOfWork, run_id: str, user_id: str
    ) -> str:
        value = unit_of_work.connection.execute(
            text(
                "SELECT conversation_id FROM runs "
                "WHERE run_id = :run_id AND user_id = :user_id"
            ),
            {"run_id": run_id, "user_id": user_id},
        ).scalar()
        if value is None:
            raise CommandError("run_not_found")
        return str(value)
