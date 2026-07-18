"""Replayable SSE cursor tests."""

import pytest

from orchestrator.sse import EventStreamError, SseEventService, StreamEvent


class Store:
    def replay(
        self, *, run_id: str, user_id: str, after_sequence: int
    ) -> tuple[StreamEvent, ...]:
        if user_id != "user_example":
            return ()
        return tuple(event for event in EVENTS if event.sequence > after_sequence)

    def detail(self, *, run_id: str, event_id: str, user_id: str) -> dict[str, object]:
        return {"category": "transition", "summary": event_id, "fields": []}


EVENTS = (
    StreamEvent(1, "evt_one", "run.started", {"status": "running"}),
    StreamEvent(2, "evt_two", "run.completed", {"status": "completed"}, terminal=True),
)


def test_sse_replays_exact_sequence_and_closes_on_terminal_event() -> None:
    service = SseEventService(Store())
    payload = service.encode(
        service.replay(run_id="run_example", user_id="user_example", after_sequence=0)
    )
    assert "id: 1" in payload and "id: 2" in payload
    assert (
        service.encode(
            service.replay(
                run_id="run_example", user_id="user_example", after_sequence=2
            )
        )
        == ": heartbeat\n\n"
    )


def test_sse_rejects_invalid_cursor_and_sequence_gaps() -> None:
    service = SseEventService(Store())
    with pytest.raises(EventStreamError, match="invalid_event_cursor"):
        service.replay(run_id="run_example", user_id="user_example", after_sequence=-1)

    class GapStore:
        def replay(
            self, *, run_id: str, user_id: str, after_sequence: int
        ) -> tuple[StreamEvent, ...]:
            return (StreamEvent(2, "evt_two", "run.started", {}),)

        def detail(
            self, *, run_id: str, event_id: str, user_id: str
        ) -> dict[str, object]:
            return {}

    with pytest.raises(EventStreamError, match="non_contiguous_event_sequence"):
        SseEventService(GapStore()).replay(
            run_id="run_example", user_id="user_example", after_sequence=0
        )
