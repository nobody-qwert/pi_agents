import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from orchestrator.domain import (
    ActorRef,
    AgentActor,
    ApprovalProposal,
    ApprovalRecord,
    AuthenticatedActor,
    CompletionProposal,
    EventEnvelope,
    EvidenceProposal,
    EvidenceRecord,
    PacketRecord,
    ProposedWorkPlan,
    RunCompletionRecord,
    TransitionProposal,
    TransitionRecord,
)
from orchestrator.domain.primitives import GitObjectHash, StrictDomainModel
from orchestrator.domain.proposals import SubmissionContext

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "schema"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_accepted_fixtures_have_deterministic_json_round_trips() -> None:
    examples = load_fixture("valid_examples.json")
    models: dict[str, type[StrictDomainModel]] = {
        "event_envelope": EventEnvelope,
        "approval_record": ApprovalRecord,
        "packet_record": PacketRecord,
        "proposed_work_plan": ProposedWorkPlan,
        "transition_proposal": TransitionProposal,
        "transition_record": TransitionRecord,
    }

    for name, model_type in models.items():
        first = model_type.model_validate_json(json.dumps(examples[name]))
        serialized = first.model_dump_json()
        second = model_type.model_validate_json(serialized)

        assert second == first
        assert second.model_dump_json() == serialized


def test_invalid_serialization_fixtures_are_rejected() -> None:
    examples = load_fixture("invalid_examples.json")
    models: dict[str, type[StrictDomainModel]] = {
        "actor_ref": ActorRef,
        "agent_actor": AgentActor,
        "event_envelope": EventEnvelope,
        "approval_record": ApprovalRecord,
        "authenticated_actor": AuthenticatedActor,
        "packet_record": PacketRecord,
        "transition_proposal": TransitionProposal,
        "transition_record": TransitionRecord,
    }

    for example in examples:
        with pytest.raises(ValidationError):
            if example["model"] == "git_object_hash":
                TypeAdapter(GitObjectHash).validate_json(json.dumps(example["payload"]))
            else:
                models[example["model"]].model_validate_json(
                    json.dumps(example["payload"])
                )


def test_valid_git_object_hash_boundaries_round_trip_deterministically() -> None:
    adapter = TypeAdapter(GitObjectHash)

    for value in load_fixture("valid_examples.json")["git_object_hashes"]:
        first = adapter.validate_json(json.dumps(value))
        serialized = adapter.dump_json(first)
        second = adapter.validate_json(serialized)

        assert second == first
        assert adapter.dump_json(second) == serialized


def submission_context() -> SubmissionContext:
    return SubmissionContext(
        proposal_id="proposal_boundary",
        run_id="run_boundary",
        work_node_id="wn_boundary",
        attempt_id="attempt_boundary",
        submitted_at=datetime(2026, 7, 16, 8, tzinfo=UTC),
        producer=AgentActor(
            actor_id="agent_boundary",
            kind="agent",
            role="boundary-test",
        ),
        design_version=1,
    )


def test_agent_proposals_cannot_validate_as_authoritative_records() -> None:
    context = submission_context()
    pairs: tuple[tuple[StrictDomainModel, type[StrictDomainModel]], ...] = (
        (
            ApprovalProposal(
                context=context,
                requested_scope="design",
                recommendation="approved",
                rationale="Agent recommendation only",
            ),
            ApprovalRecord,
        ),
        (
            EvidenceProposal(
                context=context,
                criterion_id="criterion_boundary",
                claimed_result="passed",
                summary="Agent evidence claim only",
            ),
            EvidenceRecord,
        ),
        (
            TransitionProposal(
                context=context,
                requested_next_state="VERIFIED",
                rationale="Agent transition request only",
            ),
            TransitionRecord,
        ),
        (
            CompletionProposal(
                context=context,
                claimed_criterion_ids=("criterion_boundary",),
                summary="Agent completion claim only",
            ),
            RunCompletionRecord,
        ),
    )

    for proposal, authoritative_type in pairs:
        with pytest.raises(ValidationError):
            authoritative_type.model_validate(proposal)
        with pytest.raises(ValidationError):
            authoritative_type.model_validate(proposal.model_dump())


def test_timestamps_are_normalized_to_utc() -> None:
    payload = load_fixture("valid_examples.json")["event_envelope"]
    event = EventEnvelope.model_validate_json(json.dumps(payload))

    assert event.occurred_at == datetime(2026, 7, 16, 8, tzinfo=UTC)
    assert '"occurred_at":"2026-07-16T08:00:00Z"' in event.model_dump_json()


def test_packet_fixture_preserves_complete_handoff_contract() -> None:
    payload = load_fixture("valid_examples.json")["packet_record"]
    packet = PacketRecord.model_validate_json(json.dumps(payload))

    assert packet.acceptance_criteria[0].observable_result == (
        "The strict domain schema suite passes"
    )
    assert packet.output_artifacts[0].path == "backend/schema-report.json"
    assert packet.issue_contract.redesign_in_place_allowed is False
    assert packet.output_contract.design_version_used == 1
