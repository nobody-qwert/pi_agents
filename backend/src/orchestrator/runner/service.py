"""Lease-owning runner shell around the fixed, durable LangGraph graph."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal, cast

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection

from orchestrator.runner.coordinator import RunnerCoordinator, StaleCheckpointError
from orchestrator.runner.graph import StagePort, compile_runner_graph
from orchestrator.runner.leases import (
    AutomationPausedError,
    LeaseCancelledError,
    LeaseLostError,
    PostgresRunLeaseQueue,
    RunLease,
)


@dataclass(frozen=True, slots=True)
class RunnerResult:
    run_id: str | None
    outcome: Literal[
        "completed",
        "blocked",
        "unavailable",
        "lease_lost",
        "retry_scheduled",
        "paused",
    ]


class RunnerService:
    """Claims one lease, resumes the matching LangGraph thread, then releases it."""

    def __init__(
        self,
        *,
        database_url: str,
        owner: str,
        lease_queue: PostgresRunLeaseQueue,
        coordinator: RunnerCoordinator,
        stage_port: StagePort,
    ) -> None:
        self._database_url = _psycopg_url(database_url)
        self._owner = owner
        self._lease_queue = lease_queue
        self._coordinator = coordinator
        self._stage_port = stage_port
        self._setup_lock = Lock()
        self._is_setup = False

    def initialize(self) -> None:
        """Install LangGraph's own durable checkpoint schema exactly once per process."""
        with self._setup_lock:
            if self._is_setup:
                return
            # Serialise first-use migration across independently started runners.
            with PostgresSaver.from_conn_string(self._database_url) as saver:
                connection = cast(Connection[dict[str, Any]], saver.conn)
                cursor = connection.cursor()
                try:
                    cursor.execute("SELECT pg_advisory_lock(922337203685477000)")
                    saver.setup()
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(922337203685477000)")
                    cursor.close()
            self._is_setup = True

    def run(self, run_id: str) -> RunnerResult:
        claim = self._lease_queue.claim(run_id, owner=self._owner)
        if claim.outcome == "cancelled":
            self._coordinator.stop_safely(run_id, reason="cancelled")
            self._lease_queue.acknowledge_safe_stop(run_id)
            return RunnerResult(run_id, "blocked")
        if claim.outcome == "attempts_exhausted":
            self._coordinator.stop_safely(run_id, reason="attempts_exhausted")
            self._lease_queue.acknowledge_safe_stop(run_id)
            return RunnerResult(run_id, "blocked")
        if claim.outcome in {"completed", "unavailable"}:
            return RunnerResult(run_id, "unavailable")
        assert claim.lease is not None
        return self._execute(claim.lease)

    def run_next(self) -> RunnerResult:
        pending_stop = self._lease_queue.next_safe_stop()
        if pending_stop is not None:
            run_id, reason = pending_stop
            self._coordinator.stop_safely(run_id, reason=reason)
            self._lease_queue.acknowledge_safe_stop(run_id)
            return RunnerResult(run_id, "blocked")
        claim = self._lease_queue.claim_next(owner=self._owner)
        if claim.lease is None:
            return RunnerResult(None, "unavailable")
        return self._execute(claim.lease)

    def _execute(self, lease: RunLease) -> RunnerResult:
        self.initialize()
        lease_ref = [lease]
        try:
            with PostgresSaver.from_conn_string(self._database_url) as saver:
                graph = compile_runner_graph(
                    coordinator=self._coordinator,
                    stage_port=self._stage_port,
                    lease_ref=lease_ref,
                    checkpointer=saver,
                )
                graph.invoke(
                    {},
                    config={
                        "configurable": {
                            "thread_id": lease.run_id,
                            "checkpoint_ns": "fixed-control-v1",
                        }
                    },
                )
        except LeaseCancelledError:
            self._coordinator.stop_safely(lease.run_id, reason="cancelled")
            self._lease_queue.complete(lease_ref[0])
            return RunnerResult(lease.run_id, "blocked")
        except AutomationPausedError:
            self._lease_queue.release(lease_ref[0])
            return RunnerResult(lease.run_id, "paused")
        except StaleCheckpointError:
            self._coordinator.stop_safely(lease.run_id, reason="stale_checkpoint")
            self._lease_queue.complete(lease_ref[0])
            return RunnerResult(lease.run_id, "blocked")
        except LeaseLostError:
            return RunnerResult(lease.run_id, "lease_lost")
        except Exception:
            # A port failure commits neither its transition nor its checkpoint.
            # The released lease is a bounded retry, not an in-memory retry loop.
            self._lease_queue.release(lease_ref[0])
            return RunnerResult(lease.run_id, "retry_scheduled")

        with self._coordinator._unit_of_work.transaction() as unit_of_work:
            run = unit_of_work.runs.get(lease.run_id)
        if run is not None and run.status in {"completed", "blocked", "failed"}:
            try:
                lease_ref[0] = self._lease_queue.renew(lease_ref[0])
            except LeaseLostError:
                return RunnerResult(lease.run_id, "lease_lost")
            self._lease_queue.complete(lease_ref[0])
            return RunnerResult(
                lease.run_id, "completed" if run.status == "completed" else "blocked"
            )
        self._lease_queue.release(lease_ref[0])
        return RunnerResult(lease.run_id, "retry_scheduled")


def _psycopg_url(database_url: str) -> str:
    """Convert the SQLAlchemy psycopg URL accepted by existing repositories."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
