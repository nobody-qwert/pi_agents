"""Atomic ordered event writes and authorized replay for one run."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from orchestrator.domain.events import EventDraft, EventEnvelope
from orchestrator.domain.primitives import RunId
from orchestrator.persistence.postgres import PostgresUnitOfWork

_NOTIFY_CHANNEL = "orchestrator_run_events"


class EventReplayAuthorizer(Protocol):
    """Service-bound authorization check required before event replay."""

    def can_replay_events(self, run_id: RunId) -> bool:
        """Return whether the current principal may read this run's event log."""


class EventWakeupNotifier(Protocol):
    """Best-effort wakeup channel; replay polling remains the source of recovery."""

    def notify_run_events(self, run_id: RunId) -> None:
        """Wake listeners after the event transaction has committed."""


class ReplayAccessDeniedError(PermissionError):
    """The caller was not authorized to replay a run's audit projection."""


class PostgresEventWakeupNotifier:
    """Sends PostgreSQL LISTEN/NOTIFY wakeups on a dedicated post-commit connection."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)

    def notify_run_events(self, run_id: RunId) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_notify(:channel, :run_id)"),
                {"channel": _NOTIFY_CHANNEL, "run_id": run_id},
            )

    def close(self) -> None:
        """Release the dedicated notification connection pool."""
        self._engine.dispose()


class DurableEventService:
    """Coordinates one authoritative mutation and its audit event atomically.

    A wakeup is deliberately sent only after the transaction exits successfully.
    It is best effort: a disconnected or late listener always recovers by
    replaying after its last durable sequence.
    """

    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        notifier: EventWakeupNotifier,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._notifier = notifier

    def apply(
        self,
        draft: EventDraft,
        state_change: Callable[[PostgresUnitOfWork], None],
    ) -> EventEnvelope:
        """Apply state and event together, returning the original event on retry."""
        with self._unit_of_work.transaction() as unit_of_work:
            existing = unit_of_work.events.get_by_command_idempotency_key(
                run_id=draft.run_id,
                command_idempotency_key=draft.command_idempotency_key,
            )
            if existing is not None:
                event = existing
            elif unit_of_work.events.reserve_command(
                run_id=draft.run_id,
                command_idempotency_key=draft.command_idempotency_key,
            ):
                state_change(unit_of_work)
                event = unit_of_work.events.append(draft)
            else:
                # The reservation INSERT waits for the concurrent owner to
                # commit.  Its event must therefore be visible before a retry
                # can reach this branch, and no state mutation may occur here.
                retried_event = unit_of_work.events.get_by_command_idempotency_key(
                    run_id=draft.run_id,
                    command_idempotency_key=draft.command_idempotency_key,
                )
                if retried_event is None:
                    raise RuntimeError(
                        "command reservation committed without its durable event"
                    )
                event = retried_event

        with suppress(SQLAlchemyError):
            self._notifier.notify_run_events(draft.run_id)
            # Notifications are wakeups, not a second source of truth.  A client
            # that misses one polls and replays from its durable sequence.
        return event

    def replay(
        self,
        *,
        run_id: RunId,
        after_sequence: int,
        authorizer: EventReplayAuthorizer,
    ) -> tuple[EventEnvelope, ...]:
        """Return a stable ordered event suffix after authorization succeeds."""
        if after_sequence < 0:
            raise ValueError("after_sequence must be zero or a positive sequence")
        if not authorizer.can_replay_events(run_id):
            raise ReplayAccessDeniedError(f"not authorized to replay {run_id!r}")
        with self._unit_of_work.transaction() as unit_of_work:
            return unit_of_work.events.replay(
                run_id=run_id,
                after_sequence=after_sequence,
            )
