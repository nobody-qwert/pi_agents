"""Ready queue policy tests use the canonical packet schema fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.dispatch import DispatchRejected, PacketDispatchService
from orchestrator.domain import PacketRecord, WorkNodeRecord


def packet() -> PacketRecord:
    fixture = Path(__file__).parents[1] / "fixtures/schema/valid_examples.json"
    return PacketRecord.model_validate_json(
        json.dumps(json.loads(fixture.read_text())["packet_record"])
    )


def node() -> WorkNodeRecord:
    return WorkNodeRecord.model_validate_json(
        json.dumps(
            {
                "metadata": {
                    "record_version": 1,
                    "created_at": "2026-07-16T08:02:00Z",
                    "updated_at": "2026-07-16T08:02:00Z",
                },
                "work_node_id": "wn_schema",
                "run_id": "run_0001",
                "node_type": "LEAF_TASK",
                "goal": "Define strict domain schema primitives",
                "owner_role": "executor",
                "status": "READY",
                "design_refs": [
                    {"design_version": 1, "section": "PLAN 6", "decision_ids": []}
                ],
                "outputs": ["schema report"],
                "acceptance_criterion_ids": ["criterion_schema_tests"],
            }
        )
    )


def test_packet_claim_is_idempotent_and_bounded() -> None:
    service = PacketDispatchService()
    issued = packet()
    service.issue(
        issued,
        node=node(),
        dependency_states={},
        current_design_version=1,
    )
    assert service.claim(issued.packet_id, max_attempts=2).attempt_number == 1
    assert service.claim(issued.packet_id, max_attempts=2).attempt_number == 2
    with pytest.raises(DispatchRejected, match="attempt_budget_exhausted"):
        service.claim(issued.packet_id, max_attempts=2)
