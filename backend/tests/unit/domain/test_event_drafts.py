"""Unit coverage for safe durable-event serialization boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator.domain import EventDraft


def _draft(**overrides: object) -> EventDraft:
    values: dict[str, object] = {
        "event_id": "evt_event_draft",
        "run_id": "run_event_draft",
        "conversation_id": "conv_event_draft",
        "occurred_at": datetime(2026, 7, 17, 8, tzinfo=UTC),
        "type": "transition.applied",
        "stage": "INTAKE",
        "node_id": "event-service",
        "attempt_id": "attempt_event_draft",
        "design_version": 1,
        "packet_version": 1,
        "actor_role": "event-service",
        "status": "accepted",
        "outcome": "accepted",
        "summary": "A state transition was accepted",
        "correlation_id": "command-event-draft",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef",
        "command_idempotency_key": "transition:event-draft",
    }
    values.update(overrides)
    return EventDraft.model_validate(values)


def test_event_draft_projects_only_safe_inline_detail_and_serializes_envelope() -> None:
    draft = _draft(
        detail_ref="/api/v1/runs/run_event_draft/events/evt_event_draft/detail",
        inline_detail={
            "token_count": 42,
            "duration_ms": 250,
            "policy_rule_ids": ["transition.allowed"],
        },
    )

    assert draft.inline_detail == {
        "token_count": 42,
        "duration_ms": 250,
        "policy_rule_ids": ["transition.allowed"],
    }
    envelope = draft.envelope(7)
    assert envelope.sequence == 7
    assert envelope.detail_ref == draft.detail_ref
    assert envelope.attempt_id == draft.attempt_id
    assert envelope.design_version == draft.design_version
    assert envelope.packet_version == draft.packet_version
    assert envelope.actor_role == draft.actor_role
    assert envelope.outcome == draft.outcome
    assert envelope.correlation_id == draft.correlation_id
    assert "inline_detail" not in envelope.model_dump()


def test_event_draft_rejects_oversized_inline_detail() -> None:
    with pytest.raises(ValidationError, match="safe audit projection limit"):
        _draft(inline_detail={"policy_rule_ids": ["x" * 128] * 16})


@pytest.mark.parametrize(
    "summary",
    (
        "Workspace checkpoint completed for approved work node",
        "Agent token stream started",
    ),
)
def test_event_draft_accepts_bounded_operational_summary(summary: str) -> None:
    draft = _draft(summary=summary)

    assert draft.summary == summary


@pytest.mark.parametrize(
    ("summary", "message"),
    (
        ("Model reasoning: first inspect every file", "raw reasoning"),
        ("Chain of thought recorded for the transition", "raw reasoning"),
        ("Authorization: Bearer top-secret-value", "secret-bearing"),
        ("Tool completed with api_key=sk-secret-value", "secret-bearing"),
        ("Tool completed with access_token=top-secret-value", "secret-bearing"),
        ("x" * 281, "280-character"),
    ),
)
def test_event_draft_rejects_unsafe_or_unbounded_summary(
    summary: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _draft(summary=summary)


@pytest.mark.parametrize(
    "inline_detail",
    (
        {"reasoning": "private chain of thought"},
        {"analysis": "unreviewed model analysis"},
        {"session": {"cookie": "secret"}},
        {"api_token": "sk-secret-value"},
        {"policy_rule_ids": ["sk-secret-value"]},
    ),
)
def test_event_draft_rejects_unrecognized_or_secret_bearing_detail(
    inline_detail: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match=r"safe projection|safe identifiers"):
        _draft(inline_detail=inline_detail)


def test_event_draft_requires_all_audit_trail_fields() -> None:
    for field_name in (
        "attempt_id",
        "design_version",
        "packet_version",
        "actor_role",
        "outcome",
        "correlation_id",
    ):
        values = _draft().model_dump()
        values.pop(field_name)
        with pytest.raises(ValidationError):
            EventDraft.model_validate(values)


@pytest.mark.parametrize(
    "detail_ref",
    (
        "reasoning: first inspect every file",
        "artifact://secret-bearing-tool-output",
        "https://example.test/detail?access_token=top-secret",
        "/api/v1/runs/run_other/events/evt_event_draft/detail",
        "/api/v1/runs/run_event_draft/events/evt_other/detail",
        "/api/v1/runs/run_event_draft/events/evt_event_draft/detail/extra",
        "/api/v1/runs/run_event_draft/events/evt_event_draft/detail" + "x" * 385,
    ),
)
def test_event_draft_rejects_unsafe_or_non_owning_detail_reference(
    detail_ref: str,
) -> None:
    with pytest.raises(ValidationError, match="detail_ref"):
        _draft(detail_ref=detail_ref)
