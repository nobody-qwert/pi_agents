"""Ordered, authorized SSE serialization over durable event sequence cursors."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


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
