"""Desktop grant and exclusive input ownership tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.desktop import DesktopError, DesktopGateway


class Automation:
    def __init__(self, *, pause: bool = True, resume: bool = True) -> None:
        self.pause_result = pause
        self.resume_result = resume

    def pause(self, run_id: str) -> bool:
        return self.pause_result

    def resume(self, run_id: str) -> bool:
        return self.resume_result


def test_desktop_token_is_run_user_scoped_single_use_and_revocable() -> None:
    gateway = DesktopGateway(Automation())
    grant = gateway.issue_session(run_id="run_example", user_id="user_example")
    session = gateway.authorize_websocket(
        session_id=grant.session_id,
        token=grant.token,
        run_id="run_example",
        user_id="user_example",
    )
    assert session.token_digest != grant.token
    with pytest.raises(DesktopError, match="desktop_token_replayed"):
        gateway.authorize_websocket(
            session_id=grant.session_id,
            token=grant.token,
            run_id="run_example",
            user_id="user_example",
        )
    grant = gateway.issue_session(run_id="run_example", user_id="user_example")
    with pytest.raises(DesktopError, match="desktop_session_not_authorized"):
        gateway.authorize_websocket(
            session_id=grant.session_id,
            token=grant.token,
            run_id="run_other",
            user_id="user_example",
        )
    gateway.revoke_session(session_id=grant.session_id, user_id="user_example")
    with pytest.raises(DesktopError, match="desktop_session_expired"):
        gateway.authorize_websocket(
            session_id=grant.session_id,
            token=grant.token,
            run_id="run_example",
            user_id="user_example",
        )


def test_desktop_session_expiry_and_ordered_input_handoff() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    gateway = DesktopGateway(
        Automation(), now=lambda: now, token_ttl=timedelta(seconds=1)
    )
    grant = gateway.issue_session(run_id="run_example", user_id="user_example")
    now += timedelta(seconds=1)
    with pytest.raises(DesktopError, match="desktop_session_expired"):
        gateway.authorize_websocket(
            session_id=grant.session_id,
            token=grant.token,
            run_id="run_example",
            user_id="user_example",
        )
    assert gateway.take_control(run_id="run_example", user_id="user_example") == "USER"
    assert (
        gateway.return_control(run_id="run_example", user_id="user_example") == "AGENT"
    )
    assert [event.action for event in gateway.events("run_example")] == [
        "pause_requested",
        "paused",
        "user_granted",
        "paused",
        "agent_resumed",
    ]


def test_control_is_not_granted_when_automation_cannot_pause() -> None:
    gateway = DesktopGateway(Automation(pause=False))
    with pytest.raises(DesktopError, match="automation_pause_failed"):
        gateway.take_control(run_id="run_example", user_id="user_example")
    assert gateway.owner("run_example") == "AGENT"
