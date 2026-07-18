"""Durable desktop grants and exclusive guest input ownership."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, cast
from urllib.parse import quote

from sqlalchemy import text

from orchestrator.commands import CommandError
from orchestrator.domain import EventDraft
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.services.events import DurableEventService, EventWakeupNotifier

InputOwner = Literal["AGENT", "USER", "PAUSED"]


class _NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


class PostgresDesktopService:
    """Issues replay-safe grants and serializes ownership with runner leases."""

    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        session_secret: str,
        *,
        notifier: EventWakeupNotifier | None = None,
        token_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if len(session_secret) < 32:
            raise ValueError("desktop session secret must contain at least 32 characters")
        if not timedelta(minutes=1) <= token_ttl <= timedelta(hours=1):
            raise ValueError("desktop token TTL is outside the allowed range")
        self._unit_of_work = unit_of_work
        self._secret = session_secret.encode()
        self._token_ttl = token_ttl
        self._events = DurableEventService(unit_of_work, notifier or _NoopNotifier())

    def issue_session(
        self, *, run_id: str, user_id: str, idempotency_key: str
    ) -> dict[str, object]:
        self._validate_key(idempotency_key)
        self._require_ready_workspace(run_id, user_id)
        digest = sha256(
            f"{run_id}\0{user_id}\0{idempotency_key}".encode()
        ).hexdigest()
        session_id = f"desktop_{digest[:32]}"
        token = hmac.new(self._secret, session_id.encode(), sha256).hexdigest()
        now = datetime.now(UTC)
        expires_at = now + self._token_ttl
        with self._unit_of_work.transaction() as unit_of_work:
            unit_of_work.connection.execute(
                text(
                    "INSERT INTO workspace_input_ownership "
                    "(run_id, owner, record_version, updated_at) "
                    "VALUES (:run_id, 'AGENT', 1, :now) ON CONFLICT (run_id) DO NOTHING"
                ),
                {"run_id": run_id, "now": now},
            )
            unit_of_work.connection.execute(
                text(
                    "INSERT INTO desktop_sessions "
                    "(session_id, run_id, user_id, token_digest, expires_at, created_at, "
                    "idempotency_key) VALUES (:session_id, :run_id, :user_id, "
                    ":token_digest, :expires_at, :now, :idempotency_key) "
                    "ON CONFLICT (run_id, user_id, idempotency_key) DO NOTHING"
                ),
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "user_id": user_id,
                    "token_digest": sha256(token.encode()).hexdigest(),
                    "expires_at": expires_at,
                    "now": now,
                    "idempotency_key": idempotency_key,
                },
            )
            row = unit_of_work.connection.execute(
                text(
                    "SELECT session_id, expires_at FROM desktop_sessions "
                    "WHERE run_id = :run_id AND user_id = :user_id "
                    "AND idempotency_key = :idempotency_key"
                ),
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                },
            ).one()
            owner = unit_of_work.connection.execute(
                text(
                    "SELECT owner FROM workspace_input_ownership WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
        stored_id = str(row.session_id)
        stored_token = hmac.new(self._secret, stored_id.encode(), sha256).hexdigest()
        return {
            "session_id": stored_id,
            "expires_at": row.expires_at.isoformat(),
            "websocket_url": (
                f"/desktop/ws/{stored_id}?token={quote(stored_token, safe='')}"
            ),
            "input_owner": str(owner),
            "previews": [],
        }

    def change_owner(
        self,
        *,
        run_id: str,
        user_id: str,
        requested_owner: Literal["AGENT", "USER"],
        idempotency_key: str,
    ) -> dict[str, object]:
        self._validate_key(idempotency_key)
        self._require_ready_workspace(run_id, user_id)
        owner = self._owner(run_id)
        if requested_owner == "USER":
            if owner == "AGENT":
                self._transition(
                    run_id,
                    expected="AGENT",
                    target="PAUSED",
                    key=f"desktop-pause:{run_id}:{idempotency_key}",
                    summary="Guest automation pause requested",
                )
                owner = "PAUSED"
            if owner == "PAUSED" and not self._has_live_lease(run_id):
                self._transition(
                    run_id,
                    expected="PAUSED",
                    target="USER",
                    key=f"desktop-user:{run_id}:{idempotency_key}",
                    summary="Authenticated user granted guest input",
                )
                owner = "USER"
        elif owner in {"USER", "PAUSED"}:
            self._transition(
                run_id,
                expected=owner,
                target="AGENT",
                key=f"desktop-agent:{run_id}:{idempotency_key}",
                summary="Guest input returned to automation",
            )
            owner = "AGENT"
        return self._state(run_id, owner)

    def state(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._require_ready_workspace(run_id, user_id)
        return self._state(run_id, self._owner(run_id))

    def _transition(
        self,
        run_id: str,
        *,
        expected: InputOwner,
        target: InputOwner,
        key: str,
        summary: str,
    ) -> None:
        draft = self._event_draft(run_id, key, target, summary)

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            updated = unit_of_work.connection.execute(
                text(
                    "UPDATE workspace_input_ownership SET owner = :target, "
                    "record_version = record_version + 1, updated_at = :now "
                    "WHERE run_id = :run_id AND owner = :expected RETURNING owner"
                ),
                {
                    "target": target,
                    "now": datetime.now(UTC),
                    "run_id": run_id,
                    "expected": expected,
                },
            ).scalar()
            if updated is None:
                raise CommandError("input_owner_conflict")

        self._events.apply(draft, persist)

    def _event_draft(
        self, run_id: str, key: str, target: InputOwner, summary: str
    ) -> EventDraft:
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "SELECT conversation_id, payload FROM runs WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).mappings().one_or_none()
            design_version = unit_of_work.connection.execute(
                text(
                    "SELECT COALESCE(MAX(design_version), 1) FROM design_revisions "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
        if row is None:
            raise CommandError("run_not_found")
        digest = sha256(key.encode()).hexdigest()
        event_id = f"evt_{digest[:32]}"
        trace_id = row["payload"]["metadata"].get("trace_id") or digest[:32]
        return EventDraft(
            event_id=event_id,
            run_id=run_id,
            conversation_id=str(row["conversation_id"]),
            occurred_at=datetime.now(UTC),
            type="vm.input_owner_changed",
            stage="RESUME_GATE",
            node_id="desktop-ownership-service",
            attempt_id=f"attempt_{digest[:32]}",
            design_version=int(design_version),
            packet_version=1,
            actor_role="desktop-ownership-service",
            status="paused" if target == "PAUSED" else "accepted",
            outcome="paused" if target == "PAUSED" else "accepted",
            summary=summary,
            detail_ref=f"/api/v1/runs/{run_id}/events/{event_id}/detail",
            correlation_id=key,
            trace_id=str(trace_id),
            span_id=digest[:16],
            command_idempotency_key=key,
            inline_detail={"next_state": target},
        )

    def _require_ready_workspace(self, run_id: str, user_id: str) -> None:
        with self._unit_of_work.transaction() as unit_of_work:
            status = unit_of_work.connection.execute(
                text(
                    "SELECT workspace.lifecycle_status FROM runs JOIN workspace_sessions "
                    "AS workspace USING (run_id) WHERE runs.run_id = :run_id "
                    "AND runs.user_id = :user_id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).scalar()
        if status is None:
            raise CommandError("workspace_not_ready")
        if status not in {"ready", "active"}:
            raise CommandError("workspace_not_ready")

    def _owner(self, run_id: str) -> InputOwner:
        with self._unit_of_work.transaction() as unit_of_work:
            value = unit_of_work.connection.execute(
                text(
                    "SELECT owner FROM workspace_input_ownership WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar()
        return cast(InputOwner, value or "AGENT")

    def _has_live_lease(self, run_id: str) -> bool:
        with self._unit_of_work.transaction() as unit_of_work:
            value = unit_of_work.connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM run_queue WHERE run_id = :run_id "
                    "AND completed_at IS NULL AND lease_expires_at > :now)"
                ),
                {"run_id": run_id, "now": datetime.now(UTC)},
            ).scalar_one()
        return bool(value)

    @staticmethod
    def _state(run_id: str, owner: InputOwner) -> dict[str, object]:
        return {"run_id": run_id, "input_owner": owner}

    @staticmethod
    def _validate_key(value: str) -> None:
        if not value or len(value) > 256:
            raise CommandError("missing_idempotency_key")


class DesktopSessionAuthorizer:
    """Consumes a single-use grant before a desktop byte stream is opened."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def consume(self, *, session_id: str, token: str) -> str:
        if not session_id.startswith("desktop_") or len(token) > 256:
            raise CommandError("desktop_session_not_authorized")
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        with self._unit_of_work.transaction() as unit_of_work:
            run_id = unit_of_work.connection.execute(
                text(
                    "UPDATE desktop_sessions SET websocket_used_at = :now "
                    "WHERE session_id = :session_id AND token_digest = :token_digest "
                    "AND revoked_at IS NULL AND websocket_used_at IS NULL "
                    "AND expires_at > :now RETURNING run_id"
                ),
                {
                    "session_id": session_id,
                    "token_digest": token_digest,
                    "now": datetime.now(UTC),
                },
            ).scalar()
        if run_id is None:
            raise CommandError("desktop_session_not_authorized")
        return str(run_id)
