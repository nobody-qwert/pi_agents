"""Ordered, authorized SSE serialization over durable event sequence cursors."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text

from orchestrator.persistence import PostgresUnitOfWork


class EventStreamError(Exception):
    """An event cursor or access request was rejected safely."""


@dataclass(frozen=True, slots=True)
class StreamEvent:
    sequence: int
    event_id: str
    event_type: str
    payload: dict[str, object]
    terminal: bool = False


class EventStreamStore(Protocol):
    def replay(
        self, *, run_id: str, user_id: str, after_sequence: int
    ) -> tuple[StreamEvent, ...]: ...

    def detail(
        self, *, run_id: str, event_id: str, user_id: str
    ) -> dict[str, object]: ...


class SseEventService:
    """Serializes exact durable replay; callers may poll again after a heartbeat."""

    def __init__(self, store: EventStreamStore) -> None:
        self._store = store

    def replay(
        self, *, run_id: str, user_id: str, after_sequence: int
    ) -> tuple[StreamEvent, ...]:
        if not run_id.startswith("run_") or after_sequence < 0:
            raise EventStreamError("invalid_event_cursor")
        events = self._store.replay(
            run_id=run_id, user_id=user_id, after_sequence=after_sequence
        )
        expected = after_sequence + 1
        for event in events:
            if event.sequence != expected:
                raise EventStreamError("non_contiguous_event_sequence")
            expected += 1
        return events

    def encode(self, events: Iterable[StreamEvent]) -> str:
        frames: list[str] = []
        for event in events:
            payload = {
                "event_id": event.event_id,
                "sequence": event.sequence,
                **event.payload,
            }
            frames.append(
                f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            )
            if event.terminal:
                break
        return "".join(frames) or ": heartbeat\n\n"

    def detail(self, *, run_id: str, event_id: str, user_id: str) -> dict[str, object]:
        if not run_id.startswith("run_") or not event_id.startswith("evt_"):
            raise EventStreamError("invalid_event_detail")
        return self._store.detail(run_id=run_id, event_id=event_id, user_id=user_id)


class PostgresEventStreamStore:
    """Authorized durable replay/detail adapter for the HTTP SSE boundary."""

    _TERMINAL = frozenset({"run.completed", "run.failed", "run.blocked"})

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def replay(
        self, *, run_id: str, user_id: str, after_sequence: int
    ) -> tuple[StreamEvent, ...]:
        with self._unit_of_work.transaction() as unit_of_work:
            high_water = unit_of_work.connection.execute(
                text(
                    "SELECT next_event_sequence - 1 FROM runs "
                    "WHERE run_id = :run_id AND user_id = :user_id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).scalar()
            if high_water is None:
                raise EventStreamError("run_not_found")
            if after_sequence > high_water:
                raise EventStreamError("future_event_cursor")
            envelopes = unit_of_work.events.replay(
                run_id=run_id, after_sequence=after_sequence
            )
        return tuple(
            StreamEvent(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.type,
                payload=event.model_dump(
                    mode="json", exclude={"event_id", "sequence", "type"}
                ),
                terminal=event.type in self._TERMINAL,
            )
            for event in envelopes
        )

    def detail(self, *, run_id: str, event_id: str, user_id: str) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT event.event_type, event.payload, event.inline_detail "
                        "FROM run_events AS event JOIN runs ON runs.run_id = event.run_id "
                        "WHERE event.run_id = :run_id AND event.event_id = :event_id "
                        "AND runs.user_id = :user_id"
                    ),
                    {"run_id": run_id, "event_id": event_id, "user_id": user_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise EventStreamError("event_not_found")
        payload = row["payload"]
        event_type = row["event_type"]
        fields: list[dict[str, object]] = [
            {"label": "Event type", "value": event_type},
            {"label": "Stage", "value": payload.get("stage")},
            {"label": "Status", "value": payload.get("status")},
        ]
        for key, value in (row["inline_detail"] or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                fields.append({"label": key.replace("_", " ").title(), "value": value})
        return {
            "category": self._category(event_type),
            "summary": payload.get("summary", event_type),
            "fields": fields,
        }

    @staticmethod
    def _category(event_type: str) -> str:
        prefix = event_type.partition(".")[0]
        if prefix in {
            "agent",
            "tool",
            "validation",
            "transition",
            "approval",
            "artifact",
            "promotion",
        }:
            return prefix
        if prefix in {"workspace", "vm"}:
            return "workspace"
        if event_type.endswith("failed"):
            return "error"
        return "transition"
