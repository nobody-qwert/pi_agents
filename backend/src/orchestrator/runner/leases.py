"""PostgreSQL queue leases used to fence one runner per run.

The queue is deliberately separate from the authoritative ``runs`` record.
Leases decide who may make progress; they never become audit state themselves.
Every authoritative write is guarded again in its own transaction, so an
expired holder cannot commit after a replacement holder has claimed the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_hex
from typing import Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from orchestrator.domain.primitives import (
    RunId,
    StrictDomainModel,
    UtcTimestamp,
)


class LeaseError(RuntimeError):
    """Base class for a runner no longer permitted to make progress."""


class LeaseLostError(LeaseError):
    """A lease expired, was superseded, or was released."""


class LeaseCancelledError(LeaseError):
    """Cancellation was requested before the holder could commit work."""


class LeaseBudgetExhaustedError(LeaseError):
    """The run consumed its configured lease-attempt budget."""


class QueueEntry(StrictDomainModel):
    run_id: RunId
    max_attempts: int
    attempt_count: int
    cancellation_requested_at: UtcTimestamp | None = None
    budget_exhausted_at: UtcTimestamp | None = None
    completed_at: UtcTimestamp | None = None


@dataclass(frozen=True, slots=True)
class RunLease:
    run_id: str
    owner: str
    token: str
    epoch: int
    attempt: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    outcome: Literal[
        "claimed", "unavailable", "cancelled", "attempts_exhausted", "completed"
    ]
    lease: RunLease | None = None


def _now(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


class PostgresRunLeaseQueue:
    """A short-transaction PostgreSQL queue with fenced, CAS-renewed leases."""

    def __init__(self, database_url: str, *, lease_duration: timedelta) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._lease_duration = lease_duration

    def close(self) -> None:
        self._engine.dispose()

    def enqueue(
        self,
        *,
        run_id: RunId,
        max_attempts: int,
        available_at: datetime | None = None,
    ) -> bool:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        now = _now(available_at)
        with self._engine.begin() as connection:
            inserted = connection.execute(
                text(
                    "INSERT INTO run_queue "
                    "(run_id, available_at, lease_epoch, attempt_count, max_attempts, "
                    "created_at, updated_at) "
                    "VALUES (:run_id, :available_at, 0, 0, :max_attempts, :now, :now) "
                    "ON CONFLICT (run_id) DO NOTHING RETURNING run_id"
                ),
                {
                    "run_id": run_id,
                    "available_at": now,
                    "max_attempts": max_attempts,
                    "now": now,
                },
            ).scalar()
        return inserted is not None

    def claim(
        self, run_id: RunId, *, owner: str, now: datetime | None = None
    ) -> LeaseClaim:
        return self._claim(run_id=run_id, owner=owner, now=_now(now))

    def claim_next(self, *, owner: str, now: datetime | None = None) -> LeaseClaim:
        current = _now(now)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "WITH candidate AS ("
                        " SELECT run_id FROM run_queue "
                        " WHERE available_at <= :now AND completed_at IS NULL "
                        "   AND cancellation_requested_at IS NULL "
                        "   AND attempt_count < max_attempts "
                        "   AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
                        " ORDER BY available_at, run_id FOR UPDATE SKIP LOCKED LIMIT 1"
                        "), claimed AS ("
                        " UPDATE run_queue AS queue SET "
                        " lease_owner = :owner, lease_token = :token, "
                        " lease_epoch = queue.lease_epoch + 1, "
                        " lease_expires_at = :expires_at, "
                        " attempt_count = queue.attempt_count + 1, updated_at = :now "
                        " FROM candidate WHERE queue.run_id = candidate.run_id "
                        " RETURNING queue.run_id, queue.lease_owner, queue.lease_token, "
                        " queue.lease_epoch, queue.attempt_count, queue.lease_expires_at"
                        ") SELECT * FROM claimed"
                    ),
                    self._claim_parameters(owner, current),
                )
                .mappings()
                .one_or_none()
            )
        return (
            LeaseClaim("claimed", self._lease_from_row(row))
            if row
            else LeaseClaim("unavailable")
        )

    def renew(self, lease: RunLease, *, now: datetime | None = None) -> RunLease:
        current = _now(now)
        expires_at = current + self._lease_duration
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    "UPDATE run_queue SET lease_expires_at = :expires_at, updated_at = :now "
                    "WHERE run_id = :run_id AND lease_owner = :owner "
                    "AND lease_token = :token AND lease_epoch = :epoch "
                    "AND lease_expires_at = :expected_expires_at "
                    "AND lease_expires_at > :now AND cancellation_requested_at IS NULL "
                    "AND completed_at IS NULL RETURNING lease_expires_at"
                ),
                {
                    "run_id": lease.run_id,
                    "owner": lease.owner,
                    "token": lease.token,
                    "epoch": lease.epoch,
                    "expected_expires_at": lease.expires_at,
                    "now": current,
                    "expires_at": expires_at,
                },
            ).scalar()
        if row is not None:
            return RunLease(
                run_id=lease.run_id,
                owner=lease.owner,
                token=lease.token,
                epoch=lease.epoch,
                attempt=lease.attempt,
                expires_at=row,
            )
        self._raise_not_current(lease, current)
        raise AssertionError("unreachable")

    def release(self, lease: RunLease, *, now: datetime | None = None) -> bool:
        current = _now(now)
        with self._engine.begin() as connection:
            released = connection.execute(
                text(
                    "UPDATE run_queue SET lease_owner = NULL, lease_token = NULL, "
                    "lease_expires_at = NULL, available_at = :now, updated_at = :now "
                    "WHERE run_id = :run_id AND lease_owner = :owner "
                    "AND lease_token = :token AND lease_epoch = :epoch "
                    "AND completed_at IS NULL RETURNING run_id"
                ),
                {
                    "run_id": lease.run_id,
                    "owner": lease.owner,
                    "token": lease.token,
                    "epoch": lease.epoch,
                    "now": current,
                },
            ).scalar()
        return released is not None

    def complete(self, lease: RunLease, *, now: datetime | None = None) -> bool:
        current = _now(now)
        with self._engine.begin() as connection:
            completed = connection.execute(
                text(
                    "UPDATE run_queue SET completed_at = :now, lease_owner = NULL, "
                    "lease_token = NULL, lease_expires_at = NULL, updated_at = :now "
                    "WHERE run_id = :run_id AND lease_owner = :owner "
                    "AND lease_token = :token AND lease_epoch = :epoch "
                    "AND lease_expires_at > :now RETURNING run_id"
                ),
                {
                    "run_id": lease.run_id,
                    "owner": lease.owner,
                    "token": lease.token,
                    "epoch": lease.epoch,
                    "now": current,
                },
            ).scalar()
        return completed is not None

    def acknowledge_safe_stop(
        self, run_id: RunId, *, now: datetime | None = None
    ) -> bool:
        """Remove a cancelled or exhausted unleased item after durable blocking."""
        current = _now(now)
        with self._engine.begin() as connection:
            completed = connection.execute(
                text(
                    "UPDATE run_queue SET completed_at = :now, lease_owner = NULL, "
                    "lease_token = NULL, lease_expires_at = NULL, updated_at = :now "
                    "WHERE run_id = :run_id AND completed_at IS NULL "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
                    "AND (cancellation_requested_at IS NOT NULL "
                    "OR attempt_count >= max_attempts) RETURNING run_id"
                ),
                {"run_id": run_id, "now": current},
            ).scalar()
        return completed is not None

    def request_cancellation(
        self, run_id: RunId, *, now: datetime | None = None
    ) -> bool:
        current = _now(now)
        with self._engine.begin() as connection:
            requested = connection.execute(
                text(
                    "UPDATE run_queue SET cancellation_requested_at = COALESCE("
                    "cancellation_requested_at, :now), updated_at = :now "
                    "WHERE run_id = :run_id AND completed_at IS NULL RETURNING run_id"
                ),
                {"run_id": run_id, "now": current},
            ).scalar()
        return requested is not None

    def next_safe_stop(
        self, *, now: datetime | None = None
    ) -> tuple[str, Literal["cancelled", "attempts_exhausted"]] | None:
        """Find an unheld cancellation or exhausted budget for a runner to stop."""
        current = _now(now)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT run_id, CASE WHEN cancellation_requested_at IS NOT NULL "
                        "THEN 'cancelled' ELSE 'attempts_exhausted' END AS outcome "
                        "FROM run_queue WHERE completed_at IS NULL "
                        "AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
                        "AND (cancellation_requested_at IS NOT NULL "
                        "OR attempt_count >= max_attempts) "
                        "ORDER BY updated_at, run_id FOR UPDATE SKIP LOCKED LIMIT 1"
                    ),
                    {"now": current},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if row["outcome"] == "attempts_exhausted":
                connection.execute(
                    text(
                        "UPDATE run_queue SET budget_exhausted_at = COALESCE("
                        "budget_exhausted_at, :now), updated_at = :now WHERE run_id = :run_id"
                    ),
                    {"run_id": row["run_id"], "now": current},
                )
            return row["run_id"], row["outcome"]

    def require_current(
        self, connection: Connection, lease: RunLease, *, now: datetime | None = None
    ) -> None:
        """Fence an authoritative transaction with the same lease token and epoch."""
        current = _now(now)
        active = (
            connection.execute(
                text(
                    "SELECT cancellation_requested_at FROM run_queue "
                    "WHERE run_id = :run_id AND lease_owner = :owner "
                    "AND lease_token = :token AND lease_epoch = :epoch "
                    "AND lease_expires_at > :now AND completed_at IS NULL"
                ),
                {
                    "run_id": lease.run_id,
                    "owner": lease.owner,
                    "token": lease.token,
                    "epoch": lease.epoch,
                    "now": current,
                },
            )
            .mappings()
            .one_or_none()
        )
        if active is not None and active["cancellation_requested_at"] is None:
            return
        if active is not None:
            raise LeaseCancelledError(f"cancellation requested for {lease.run_id}")
        self._raise_not_current(lease, current, connection=connection)

    def entry(self, run_id: RunId) -> QueueEntry | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT run_id, max_attempts, attempt_count, cancellation_requested_at, "
                        "budget_exhausted_at, completed_at FROM run_queue WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one_or_none()
            )
        return QueueEntry.model_validate(row) if row else None

    def _claim(self, *, run_id: RunId, owner: str, now: datetime) -> LeaseClaim:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "UPDATE run_queue SET lease_owner = :owner, lease_token = :token, "
                        "lease_epoch = lease_epoch + 1, lease_expires_at = :expires_at, "
                        "attempt_count = attempt_count + 1, updated_at = :now "
                        "WHERE run_id = :run_id AND available_at <= :now "
                        "AND completed_at IS NULL AND cancellation_requested_at IS NULL "
                        "AND attempt_count < max_attempts "
                        "AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
                        "RETURNING run_id, lease_owner, lease_token, lease_epoch, attempt_count, lease_expires_at"
                    ),
                    {"run_id": run_id, **self._claim_parameters(owner, now)},
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                return LeaseClaim("claimed", self._lease_from_row(row))
            entry = (
                connection.execute(
                    text(
                        "SELECT cancellation_requested_at, completed_at, attempt_count, max_attempts "
                        "FROM run_queue WHERE run_id = :run_id FOR UPDATE"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one_or_none()
            )
            if entry is None:
                return LeaseClaim("unavailable")
            if entry["completed_at"] is not None:
                return LeaseClaim("completed")
            if entry["cancellation_requested_at"] is not None:
                return LeaseClaim("cancelled")
            if entry["attempt_count"] >= entry["max_attempts"]:
                connection.execute(
                    text(
                        "UPDATE run_queue SET budget_exhausted_at = COALESCE("
                        "budget_exhausted_at, :now), updated_at = :now WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id, "now": now},
                )
                return LeaseClaim("attempts_exhausted")
            return LeaseClaim("unavailable")

    def _claim_parameters(self, owner: str, now: datetime) -> dict[str, object]:
        return {
            "owner": owner,
            "token": token_hex(24),
            "now": now,
            "expires_at": now + self._lease_duration,
        }

    @staticmethod
    def _lease_from_row(row: object) -> RunLease:
        values = row  # SQLAlchemy RowMapping is deliberately mapping-like.
        return RunLease(
            run_id=values["run_id"],  # type: ignore[index]
            owner=values["lease_owner"],  # type: ignore[index]
            token=values["lease_token"],  # type: ignore[index]
            epoch=values["lease_epoch"],  # type: ignore[index]
            attempt=values["attempt_count"],  # type: ignore[index]
            expires_at=values["lease_expires_at"],  # type: ignore[index]
        )

    def _raise_not_current(
        self,
        lease: RunLease,
        now: datetime,
        *,
        connection: Connection | None = None,
    ) -> None:
        if connection is None:
            with self._engine.connect() as owned_connection:
                self._raise_not_current(lease, now, connection=owned_connection)
            return
        cancelled = connection.execute(
            text(
                "SELECT cancellation_requested_at IS NOT NULL FROM run_queue "
                "WHERE run_id = :run_id"
            ),
            {"run_id": lease.run_id},
        ).scalar()
        if cancelled:
            raise LeaseCancelledError(f"cancellation requested for {lease.run_id}")
        raise LeaseLostError(f"lease for {lease.run_id} is no longer current")
