"""Deterministic executor-report and independent local-verification acceptance."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.checkpoints import CheckpointService, WorkspaceCheckpoint
from orchestrator.domain.authoritative import PacketRecord
from orchestrator.domain.reports import VerificationReport, WorkReport


class DeliveryError(Exception):
    """A report cannot advance the fixed delivery stages."""


@dataclass(frozen=True, slots=True)
class ExecutionAcceptance:
    packet_id: str
    report_id: str
    status: str


@dataclass(frozen=True, slots=True)
class LocalVerificationAcceptance:
    packet_id: str
    report_id: str
    status: str
    checkpoint: WorkspaceCheckpoint | None
    issue_reason: str | None = None


class DeliveryStageService:
    """Binds reports to a packet and permits only independent verifier acceptance."""

    def __init__(self, checkpoints: CheckpointService) -> None:
        self._checkpoints = checkpoints
        self._executions: dict[str, ExecutionAcceptance] = {}
        self._verifications: dict[str, LocalVerificationAcceptance] = {}

    def accept_execution(
        self, packet: PacketRecord, report: WorkReport
    ) -> ExecutionAcceptance:
        submission = report.context.submission
        if (
            submission.producer.role != "executor"
            or submission.run_id != packet.run_id
            or submission.work_node_id != packet.work_node_id
            or submission.design_version != packet.output_contract.design_version_used
        ):
            raise DeliveryError("executor_report_not_packet_bound")
        existing = self._executions.get(packet.packet_id)
        if existing is not None:
            if existing.report_id == report.context.report_id:
                return existing
            raise DeliveryError("packet_execution_already_accepted")
        if report.status != "implemented":
            accepted = ExecutionAcceptance(
                packet.packet_id, report.context.report_id, "issue"
            )
        else:
            accepted = ExecutionAcceptance(
                packet.packet_id, report.context.report_id, "implemented"
            )
        self._executions[packet.packet_id] = accepted
        return accepted

    def accept_local_verification(
        self,
        packet: PacketRecord,
        report: VerificationReport,
        *,
        workspace_id: str,
        checkpoint_id: str,
    ) -> LocalVerificationAcceptance:
        execution = self._executions.get(packet.packet_id)
        if execution is None or execution.status != "implemented":
            raise DeliveryError("no_accepted_execution")
        submission = report.context.submission
        if (
            submission.producer.role != "local-verifier"
            or submission.run_id != packet.run_id
            or report.work_node_id != packet.work_node_id
            or submission.work_node_id != packet.work_node_id
            or submission.design_version != packet.output_contract.design_version_used
        ):
            raise DeliveryError("verifier_report_not_packet_bound")
        existing = self._verifications.get(packet.packet_id)
        if existing is not None:
            if existing.report_id == report.context.report_id:
                return existing
            raise DeliveryError("packet_verification_already_accepted")
        expected = {criterion.criterion_id for criterion in packet.acceptance_criteria}
        passed = {
            criterion.criterion_id
            for criterion in report.criterion_results
            if criterion.result == "passed"
        }
        if report.verdict != "passed" or passed != expected:
            result = LocalVerificationAcceptance(
                packet.packet_id,
                report.context.report_id,
                "issue",
                None,
                "criteria_not_verified",
            )
            self._verifications[packet.packet_id] = result
            return result
        checkpoint = self._checkpoints.create(
            workspace_id=workspace_id,
            checkpoint_id=checkpoint_id,
            kind="service_accepted",
            design_version=submission.design_version,
            work_node_id=packet.work_node_id,
            evidence_ids=(),
        )
        result = LocalVerificationAcceptance(
            packet.packet_id, report.context.report_id, "locally_verified", checkpoint
        )
        self._verifications[packet.packet_id] = result
        return result
