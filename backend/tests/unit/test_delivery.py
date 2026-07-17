"""Packet-bound delivery stage tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from orchestrator.checkpoints import CheckpointService
from orchestrator.delivery import DeliveryError, DeliveryStageService
from orchestrator.domain.authoritative import (
    AcceptanceCheck,
    IssueContract,
    OutputArtifactSpecification,
    OutputContract,
    PacketAcceptanceCriterion,
    PacketRecord,
)
from orchestrator.domain.primitives import AgentActor, RecordMetadata
from orchestrator.domain.proposals import SubmissionContext
from orchestrator.domain.reports import ReportContext, WorkReport


def packet() -> PacketRecord:
    now = datetime.now(UTC)
    return PacketRecord(
        packet_id="pkt_example",
        run_id="run_example",
        task_id="task_example",
        work_node_id="wn_example",
        task_type="LEAF_TASK",
        goal="Do the work",
        design_baseline=(),
        acceptance_criteria=(
            PacketAcceptanceCriterion(
                criterion_id="criterion_example", observable_result="works"
            ),
        ),
        output_artifacts=(
            OutputArtifactSpecification(path="result.txt", required_form="text"),
        ),
        acceptance_checks=(
            AcceptanceCheck(method="command", procedure="test", evidence="output"),
        ),
        authority_limits=("guest only",),
        issue_contract=IssueContract(
            report_evidence="report", proposed_classifications=("LOCAL_DEFECT",)
        ),
        output_contract=OutputContract(
            status="status",
            outputs="outputs",
            checks="checks",
            risks="risks",
            issues="issues",
            design_version_used=1,
        ),
        metadata=RecordMetadata(record_version=1, created_at=now, updated_at=now),
    )


def report(role: str) -> WorkReport:
    now = datetime.now(UTC)
    return WorkReport(
        context=ReportContext(
            report_id="report_example",
            submission=SubmissionContext(
                proposal_id="proposal_example",
                run_id="run_example",
                work_node_id="wn_example",
                attempt_id="attempt_example",
                submitted_at=now,
                producer=AgentActor(
                    actor_id=f"agent_{role.replace('-', '_')}", kind="agent", role=role
                ),
                design_version=1,
            ),
            reported_at=now,
        ),
        status="implemented",
    )


def test_execution_reports_must_be_packet_bound_and_executor_owned() -> None:
    service = DeliveryStageService(cast(CheckpointService, object()))
    accepted = service.accept_execution(packet(), report("executor"))
    assert accepted.status == "implemented"
    with pytest.raises(DeliveryError, match="executor_report_not_packet_bound"):
        service.accept_execution(packet(), report("local-verifier"))
