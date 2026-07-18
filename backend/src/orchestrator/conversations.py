"""Durable owned conversation and message commands."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from sqlalchemy import text

from orchestrator.commands import CommandError, RunCommand
from orchestrator.persistence import PostgresUnitOfWork


class ConversationRunCommands(Protocol):
    def create_in_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        project_id: str,
        message: str,
        idempotency_key: str,
    ) -> RunCommand: ...


class PostgresConversationService:
    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        run_commands: ConversationRunCommands,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._runs = run_commands

    def create(self, *, user_id: str, idempotency_key: str) -> dict[str, object]:
        self._validate_user(user_id)
        self._validate_key(idempotency_key)
        digest = sha256(f"{user_id}\0{idempotency_key}".encode()).hexdigest()
        conversation_id = f"conv_{digest[:24]}"
        now = datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            connection = unit_of_work.connection
            connection.execute(
                text(
                    "INSERT INTO users (user_id, tenant_id, display_name, created_at) "
                    "VALUES (:user_id, 'tenant_local', :user_id, :now) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"user_id": user_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO conversations "
                    "(conversation_id, user_id, tenant_id, created_at) "
                    "VALUES (:conversation_id, :user_id, 'tenant_local', :now) "
                    "ON CONFLICT (conversation_id) DO NOTHING"
                ),
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "now": now,
                },
            )
        return self.get(conversation_id=conversation_id, user_id=user_id)

    def list(self, *, user_id: str) -> dict[str, object]:
        self._validate_user(user_id)
        with self._unit_of_work.transaction() as unit_of_work:
            rows = unit_of_work.connection.execute(
                text(
                    "SELECT conversation.conversation_id, conversation.created_at, "
                    "(SELECT content FROM messages WHERE conversation_id = "
                    "conversation.conversation_id ORDER BY sequence DESC LIMIT 1) "
                    "AS last_message, (SELECT COUNT(*) FROM runs WHERE conversation_id = "
                    "conversation.conversation_id) AS run_count FROM conversations AS "
                    "conversation WHERE conversation.user_id = :user_id "
                    "ORDER BY conversation.created_at DESC, conversation.conversation_id"
                ),
                {"user_id": user_id},
            ).mappings()
            return {
                "conversations": [
                    {
                        "conversation_id": row["conversation_id"],
                        "created_at": row["created_at"].isoformat(),
                        "last_message": row["last_message"],
                        "run_count": row["run_count"],
                    }
                    for row in rows
                ]
            }

    def get(self, *, conversation_id: str, user_id: str) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            connection = unit_of_work.connection
            created_at = connection.execute(
                text(
                    "SELECT created_at FROM conversations WHERE "
                    "conversation_id = :conversation_id AND user_id = :user_id"
                ),
                {"conversation_id": conversation_id, "user_id": user_id},
            ).scalar()
            if created_at is None:
                raise CommandError("conversation_not_found")
            messages = connection.execute(
                text(
                    "SELECT message_id, sequence, role, content, created_at FROM messages "
                    "WHERE conversation_id = :conversation_id ORDER BY sequence"
                ),
                {"conversation_id": conversation_id},
            ).mappings()
            runs = connection.execute(
                text(
                    "SELECT run_id FROM runs WHERE conversation_id = :conversation_id "
                    "ORDER BY created_at, run_id"
                ),
                {"conversation_id": conversation_id},
            ).scalars()
            return {
                "conversation_id": conversation_id,
                "created_at": created_at.isoformat(),
                "messages": [self._message(row) for row in messages],
                "run_ids": [str(value) for value in runs],
            }

    def add_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        content: str,
        project_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        content = content.strip()
        self._validate_key(idempotency_key)
        if not content or len(content) > 16_384:
            raise CommandError("invalid_message")
        if project_id is not None:
            run = self._runs.create_in_conversation(
                user_id=user_id,
                conversation_id=conversation_id,
                project_id=project_id,
                message=content,
                idempotency_key=idempotency_key,
            )
            conversation = self.get(conversation_id=conversation_id, user_id=user_id)
            messages = conversation["messages"]
            assert isinstance(messages, list)
            return {"message": messages[-1], "run_id": run.run_id}

        digest = sha256(f"{conversation_id}\0{idempotency_key}".encode()).hexdigest()
        message_id = f"msg_{digest[:24]}"
        now = datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            connection = unit_of_work.connection
            locked = connection.execute(
                text(
                    "SELECT conversation_id FROM conversations WHERE "
                    "conversation_id = :conversation_id AND user_id = :user_id "
                    "FOR UPDATE"
                ),
                {"conversation_id": conversation_id, "user_id": user_id},
            ).scalar()
            if locked is None:
                raise CommandError("conversation_not_found")
            existing = (
                connection.execute(
                    text(
                        "SELECT message_id, sequence, role, content, created_at FROM messages "
                        "WHERE message_id = :message_id"
                    ),
                    {"message_id": message_id},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["content"] != content:
                    raise CommandError("idempotency_conflict")
                return {"message": self._message(existing), "run_id": None}
            sequence = int(
                connection.execute(
                    text(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages "
                        "WHERE conversation_id = :conversation_id"
                    ),
                    {"conversation_id": conversation_id},
                ).scalar_one()
            )
            connection.execute(
                text(
                    "INSERT INTO messages (message_id, conversation_id, sequence, "
                    "role, content, created_at) VALUES (:message_id, :conversation_id, "
                    ":sequence, 'user', :content, :now)"
                ),
                {
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "sequence": sequence,
                    "content": content,
                    "now": now,
                },
            )
        return {
            "message": {
                "message_id": message_id,
                "sequence": sequence,
                "role": "user",
                "content": content,
                "created_at": now.isoformat(),
            },
            "run_id": None,
        }

    @staticmethod
    def _message(row: object) -> dict[str, object]:
        return {
            "message_id": row["message_id"],  # type: ignore[index]
            "sequence": row["sequence"],  # type: ignore[index]
            "role": row["role"],  # type: ignore[index]
            "content": row["content"],  # type: ignore[index]
            "created_at": row["created_at"].isoformat(),  # type: ignore[index]
        }

    @staticmethod
    def _validate_user(user_id: str) -> None:
        if not user_id.startswith("user_") or len(user_id) > 128:
            raise CommandError("invalid_user")

    @staticmethod
    def _validate_key(value: str) -> None:
        if not value or len(value) > 256:
            raise CommandError("missing_idempotency_key")
