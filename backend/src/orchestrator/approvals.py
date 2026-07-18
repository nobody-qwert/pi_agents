"""Durable pending authority requests and authenticated decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, cast

from sqlalchemy import text

from orchestrator.commands import CommandError
from orchestrator.domain import (
    ApprovalRecord,
    AuthenticatedActor,
    AuthorityGrant,
    EventDraft,
    RecordMetadata,
)
from orchestrator.domain.primitives import AuthorityScope
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.services.events import DurableEventService, EventWakeupNotifier


class _NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


class PostgresApprovalService:
    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        notifier: EventWakeupNotifier | None = None,
        *,
        approval_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if not timedelta(minutes=5) <= approval_ttl <= timedelta(days=30):
            raise ValueError("approval TTL is outside the allowed range")
        self._unit_of_work = unit_of_work
        self._events = DurableEventService(unit_of_work, notifier or _NoopNotifier())
        self._approval_ttl = approval_ttl

    def request(
        self,
        *,
        run_id: str,
        authority: str,
        affected_versions: tuple[str, ...],
        idempotency_key: str,
    ) -> str:
        approval_id = (
            "approval_"
            + sha256(f"{run_id}\0{idempotency_key}".encode()).hexdigest()[:32]
        )
        now = datetime.now(UTC)
        expires_at = now + self._approval_ttl
        key = f"approval-request:{approval_id}"
        draft = self._event_draft(
            run_id=run_id,
            key=key,
            event_type="approval.requested",
            status="blocked",
            summary="Authenticated authority decision requested",
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            unit_of_work.connection.execute(
                text(
                    "INSERT INTO approval_requests "
                    "(approval_id, run_id, authority, affected_versions, status, "
                    "requested_at, expires_at, requested_record_version, "
                    "idempotency_key) SELECT "
                    ":approval_id, CAST(:run_id AS varchar(128)), :authority, "
                    "CAST(:versions AS jsonb), "
                    "'pending', :now, :expires_at, "
                    "COALESCE((payload -> 'metadata' ->> 'record_version')::integer, 1) + 1, "
                    ":key FROM runs WHERE run_id = CAST(:run_id AS varchar(128)) "
                    "ON CONFLICT (run_id, idempotency_key) DO NOTHING"
                ),
                {
                    "approval_id": approval_id,
                    "run_id": run_id,
                    "authority": authority,
                    "versions": _json_array(affected_versions),
                    "now": now,
                    "expires_at": expires_at,
                    "key": idempotency_key,
                },
            )
        self._events.apply(draft, persist)
        return approval_id

    def decide(
        self,
        *,
        run_id: str,
        approval_id: str,
        user_id: str,
        decision: Literal["approved", "rejected"],
        comment: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key or len(idempotency_key) > 256:
            raise CommandError("missing_idempotency_key")
        if comment is not None:
            comment = comment.strip() or None
            if comment is not None and len(comment) > 4096:
                raise CommandError("invalid_approval_comment")
        existing = self._request_row(run_id, approval_id, user_id)
        now = datetime.now(UTC)
        if existing["status"] != "pending":
            if existing["status"] != decision:
                raise CommandError("approval_already_decided")
            return self._projection(
                approval_id,
                str(existing["authority"]),
                tuple(str(value) for value in cast(list[object], existing["affected_versions"])),
                str(existing["status"]),
                cast(str | None, existing["comment"]),
                cast(datetime, existing["expires_at"]),
            )
        if cast(datetime, existing["expires_at"]) <= now:
            raise CommandError("approval_expired")
        if existing["requested_record_version"] != existing["current_record_version"]:
            raise CommandError("approval_stale")
        key = f"approval-decision:{approval_id}:{decision}"
        draft = self._event_draft(
            run_id=run_id,
            key=key,
            event_type="approval.recorded",
            status="accepted" if decision == "approved" else "rejected",
            summary=f"Human authority decision {decision}",
            actor_role="approval-authority",
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT request.authority, request.affected_versions, request.status, "
                        "request.expires_at, request.requested_record_version, runs.payload "
                        "FROM approval_requests AS request JOIN runs USING (run_id) "
                        "WHERE request.approval_id = :approval_id AND request.run_id = :run_id "
                        "AND runs.user_id = :user_id FOR UPDATE"
                    ),
                    {
                        "approval_id": approval_id,
                        "run_id": run_id,
                        "user_id": user_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise CommandError("approval_not_found")
            if row["status"] != "pending":
                if row["status"] != decision:
                    raise CommandError("approval_already_decided")
                return
            if row["expires_at"] <= now:
                raise CommandError("approval_expired")
            run_payload = row["payload"]
            record_version = int(run_payload["metadata"]["record_version"])
            if int(row["requested_record_version"]) != record_version:
                raise CommandError("approval_stale")
            authority = str(row["authority"])
            actor = AuthenticatedActor(
                actor_id=user_id,
                kind="human",
                role="approval-authority",
                authenticated_at=now,
                authentication_context="development-identity-boundary",
            )
            record = ApprovalRecord(
                approval_id=approval_id,
                run_id=run_id,
                approver=actor,
                authority=AuthorityGrant(
                    scope=_authority_scope(authority),
                    source="approval-request",
                    granted_at=now,
                ),
                decision=decision,
                decided_at=now,
                affected_record_version=record_version,
                comment=comment,
                metadata=RecordMetadata(
                    record_version=1,
                    created_at=now,
                    updated_at=now,
                    idempotency_key=key,
                    trace_id=sha256(f"{run_id}:{approval_id}".encode()).hexdigest()[
                        :32
                    ],
                ),
            )
            unit_of_work.approvals.add(record)
            unit_of_work.connection.execute(
                text(
                    "UPDATE approval_requests SET status = :decision, decided_at = :now, "
                    "decided_by = :user_id, comment = :comment WHERE approval_id = :approval_id"
                ),
                {
                    "decision": decision,
                    "now": now,
                    "user_id": user_id,
                    "comment": comment,
                    "approval_id": approval_id,
                },
            )
        self._events.apply(draft, persist)
        decided = self._request_row(run_id, approval_id, user_id)
        return self._projection(
            approval_id,
            str(decided["authority"]),
            tuple(
                str(value)
                for value in cast(list[object], decided["affected_versions"])
            ),
            str(decided["status"]),
            cast(str | None, decided["comment"]),
            cast(datetime, decided["expires_at"]),
        )

    def _request_row(
        self, run_id: str, approval_id: str, user_id: str
    ) -> dict[str, object]:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT request.authority, request.affected_versions, request.status, "
                        "request.comment, request.expires_at, "
                        "request.requested_record_version, "
                        "COALESCE((runs.payload -> 'metadata' ->> 'record_version')::integer, 1) "
                        "AS current_record_version FROM approval_requests AS request "
                        "JOIN runs USING (run_id) WHERE request.approval_id = :approval_id "
                        "AND request.run_id = :run_id AND runs.user_id = :user_id"
                    ),
                    {
                        "approval_id": approval_id,
                        "run_id": run_id,
                        "user_id": user_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise CommandError("approval_not_found")
        return dict(row)

    def _event_draft(
        self,
        *,
        run_id: str,
        key: str,
        event_type: Literal["approval.requested", "approval.recorded"],
        status: Literal["blocked", "accepted", "rejected"],
        summary: str,
        actor_role: str = "approval-service",
    ) -> EventDraft:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT conversation_id, payload FROM runs WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one_or_none()
            )
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
        trace_id = row["payload"]["metadata"].get("trace_id") or digest[:32]
        event_id = f"evt_{digest[:32]}"
        return EventDraft(
            event_id=event_id,
            run_id=run_id,
            conversation_id=str(row["conversation_id"]),
            occurred_at=datetime.now(UTC),
            type=event_type,
            stage="USER_APPROVAL",
            node_id="approval-service",
            attempt_id=f"attempt_{digest[:32]}",
            design_version=int(design_version),
            packet_version=1,
            actor_role=actor_role,
            status=status,
            outcome=status,
            summary=summary,
            detail_ref=f"/api/v1/runs/{run_id}/events/{event_id}/detail",
            correlation_id=key,
            trace_id=str(trace_id),
            span_id=digest[:16],
            command_idempotency_key=key,
        )

    @staticmethod
    def _projection(
        approval_id: str,
        authority: str,
        versions: tuple[str, ...],
        status: str,
        comment: str | None,
        expires_at: datetime,
    ) -> dict[str, object]:
        return {
            "approval_id": approval_id,
            "authority": authority,
            "affected_versions": list(versions),
            "expires_at": expires_at.isoformat(),
            "status": status,
            "comment": comment,
        }


def _json_array(values: tuple[str, ...]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _authority_scope(value: str) -> AuthorityScope:
    if value in {
        "charter",
        "design",
        "work_plan",
        "verification",
        "transition",
        "workspace",
        "promotion",
        "completion",
    }:
        return cast(AuthorityScope, value)
    return "design"
