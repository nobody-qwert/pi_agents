"""Run-scoped noVNC session authorization and exclusive input ownership."""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Literal, Protocol

InputOwner = Literal["AGENT", "USER", "PAUSED"]
DesktopAction = Literal["pause_requested", "user_granted", "agent_resumed", "paused"]


class DesktopError(Exception):
    """A desktop session or ownership request was rejected safely."""


class AutomationControl(Protocol):
    def pause(self, run_id: str) -> bool: ...

    def resume(self, run_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class DesktopSession:
    session_id: str
    run_id: str
    user_id: str
    expires_at: datetime
    token_digest: str
    revoked: bool = False
    websocket_used: bool = False


@dataclass(frozen=True, slots=True)
class DesktopSessionGrant:
    session_id: str
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DesktopEvent:
    sequence: int
    run_id: str
    owner: InputOwner
    action: DesktopAction


class DesktopGateway:
    """Issues short-lived single-use display grants and serializes input changes."""

    def __init__(
        self,
        automation: AutomationControl,
        *,
        now: Callable[[], datetime] | None = None,
        token_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if token_ttl <= timedelta():
            raise ValueError("token_ttl must be positive")
        self._automation = automation
        self._now = now or (lambda: datetime.now(UTC))
        self._token_ttl = token_ttl
        self._sessions: dict[str, DesktopSession] = {}
        self._owners: dict[str, InputOwner] = {}
        self._events: list[DesktopEvent] = []
        self._locks: dict[str, threading.Lock] = {}

    def issue_session(self, *, run_id: str, user_id: str) -> DesktopSessionGrant:
        self._validate_identity(run_id, "run_")
        self._validate_identity(user_id, "user_")
        token = secrets.token_urlsafe(32)
        session_id = "desktop_" + secrets.token_urlsafe(18)
        expires_at = self._now() + self._token_ttl
        self._sessions[session_id] = DesktopSession(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            expires_at=expires_at,
            token_digest=self._digest(token),
        )
        self._owners.setdefault(run_id, "AGENT")
        return DesktopSessionGrant(
            session_id=session_id, token=token, expires_at=expires_at
        )

    def authorize_websocket(
        self, *, session_id: str, token: str, run_id: str, user_id: str
    ) -> DesktopSession:
        session = self._require_session(session_id, token, run_id, user_id)
        if session.websocket_used:
            raise DesktopError("desktop_token_replayed")
        session = DesktopSession(
            session_id=session.session_id,
            run_id=session.run_id,
            user_id=session.user_id,
            expires_at=session.expires_at,
            token_digest=session.token_digest,
            revoked=session.revoked,
            websocket_used=True,
        )
        self._sessions[session_id] = session
        return session

    def revoke_session(self, *, session_id: str, user_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.user_id != user_id:
            raise DesktopError("unknown_desktop_session")
        self._sessions[session_id] = DesktopSession(
            session_id=session.session_id,
            run_id=session.run_id,
            user_id=session.user_id,
            expires_at=session.expires_at,
            token_digest=session.token_digest,
            revoked=True,
            websocket_used=session.websocket_used,
        )

    def take_control(self, *, run_id: str, user_id: str) -> InputOwner:
        self._validate_identity(user_id, "user_")
        with self._lock(run_id):
            if self._owners.get(run_id, "AGENT") != "AGENT":
                raise DesktopError("input_owner_conflict")
            self._record(run_id, "PAUSED", "pause_requested")
            if not self._automation.pause(run_id):
                self._record(run_id, "AGENT", "agent_resumed")
                raise DesktopError("automation_pause_failed")
            self._owners[run_id] = "PAUSED"
            self._record(run_id, "PAUSED", "paused")
            self._owners[run_id] = "USER"
            self._record(run_id, "USER", "user_granted")
            return "USER"

    def return_control(self, *, run_id: str, user_id: str) -> InputOwner:
        self._validate_identity(user_id, "user_")
        with self._lock(run_id):
            if self._owners.get(run_id) != "USER":
                raise DesktopError("input_owner_conflict")
            self._owners[run_id] = "PAUSED"
            self._record(run_id, "PAUSED", "paused")
            if not self._automation.resume(run_id):
                raise DesktopError("automation_resume_failed")
            self._owners[run_id] = "AGENT"
            self._record(run_id, "AGENT", "agent_resumed")
            return "AGENT"

    def owner(self, run_id: str) -> InputOwner:
        return self._owners.get(run_id, "AGENT")

    def events(self, run_id: str) -> tuple[DesktopEvent, ...]:
        return tuple(event for event in self._events if event.run_id == run_id)

    def _require_session(
        self, session_id: str, token: str, run_id: str, user_id: str
    ) -> DesktopSession:
        session = self._sessions.get(session_id)
        if session is None or session.run_id != run_id or session.user_id != user_id:
            raise DesktopError("desktop_session_not_authorized")
        if session.revoked or session.expires_at <= self._now():
            raise DesktopError("desktop_session_expired")
        if not compare_digest(session.token_digest, self._digest(token)):
            raise DesktopError("desktop_session_not_authorized")
        return session

    def _record(self, run_id: str, owner: InputOwner, action: DesktopAction) -> None:
        self._events.append(DesktopEvent(len(self._events) + 1, run_id, owner, action))

    def _lock(self, run_id: str) -> threading.Lock:
        return self._locks.setdefault(run_id, threading.Lock())

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _validate_identity(value: str, prefix: str) -> None:
        if not value.startswith(prefix) or len(value) <= len(prefix):
            raise DesktopError("invalid_identity")
