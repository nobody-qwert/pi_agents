"""Production pre-delivery stage application boundary.

Model output is always recorded as an untrusted attempt before deterministic
services may write authoritative charter, design, or work-plan state.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final, Protocol, TypeVar, cast

from pydantic import JsonValue
from sqlalchemy import text

from orchestrator.approvals import PostgresApprovalService
from orchestrator.artifacts import ArtifactService
from orchestrator.artifacts.models import (
    ArtifactAccessRequest,
    ArtifactPublishRequest,
    ArtifactReference,
    ArtifactScope,
    ArtifactVersionRecord,
    artifact_storage_key,
)
from orchestrator.artifacts.ports import ArtifactVersionConflictError
from orchestrator.attempts import PostgresAgentAttemptStore, StoredAttempt
from orchestrator.checkpoints import PostgresCheckpointService
from orchestrator.domain import (
    AcceptanceCheck,
    ArtifactPointer,
    ArtifactRecord,
    AuthenticatedActor,
    AuthorityGrant,
    CharterProposal,
    CharterRecord,
    ControlStage,
    DesignCritiqueReport,
    DesignProposal,
    DesignRevision,
    EventDraft,
    EvidenceRecord,
    IntegrationReport,
    InvestigationReport,
    IssueContract,
    IssueRecord,
    IssueReport,
    OutcomeEvidence,
    OutputArtifactSpecification,
    OutputContract,
    PacketAcceptanceCriterion,
    PacketRecord,
    ProposedWorkPlan,
    RecordMetadata,
    RunCompletionRecord,
    RunRecord,
    StrictDomainModel,
    VerificationReport,
    WorkNodeRecord,
    WorkReport,
)
from orchestrator.domain.primitives import WorkNodeStatus
from orchestrator.graph.registry import AgentRegistry
from orchestrator.guest_model_gateway import GuestPiModelGateway, PiInvocationPort
from orchestrator.invocation import (
    AgentInvocationService,
    InvocationInput,
    InvocationRejected,
)
from orchestrator.model_gateway import ModelGateway
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.promotion_preview import ChangedPath
from orchestrator.runner.coordinator import StageResult, StageStatus
from orchestrator.runner.leases import AutomationPausedError
from orchestrator.services.events import DurableEventService, EventWakeupNotifier
from orchestrator.stages import AcceptedPlan, PreDeliveryStageService
from orchestrator.telemetry import NoopTelemetrySink, SafeTelemetry
from orchestrator.triage import route_issue
from orchestrator.vm import GuestHandle, PostgresVmLifecycleService
from orchestrator.workspace import WorkspaceImport, WorkspaceImportService

ResultT = TypeVar("ResultT", bound=StrictDomainModel)
_STAGE_AGENTS: Final = {
    "INTAKE": "intake",
    "INVESTIGATE": "investigator",
    "DESIGN": "design-authority",
    "DESIGN_CRITIQUE": "design-critic",
    "PLAN": "work-planner",
    "EXECUTE": "executor",
    "LOCAL_VERIFY": "local-verifier",
    "INTEGRATE": "integrator",
    "OUTCOME_VERIFY": "outcome-verifier",
    "TRIAGE": "issue-triager",
}


class StageApplicationError(RuntimeError):
    """A production stage cannot safely produce a bounded graph status."""


class GuestOutputPort(Protocol):
    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]: ...

    def export_patch(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _RunContext:
    run: RunRecord
    user_id: str
    conversation_id: str
    project_id: str
    source_fingerprint: str
    queue_attempt: int


class ProductionPreDeliveryStagePort:
    """Executes the fixed graph and accepts each model claim durably."""

    def __init__(
        self,
        *,
        unit_of_work: PostgresUnitOfWork,
        registry: AgentRegistry,
        gateway: ModelGateway,
        artifacts: ArtifactService,
        notifier: EventWakeupNotifier,
        lifecycle: PostgresVmLifecycleService | None = None,
        imports: WorkspaceImportService | None = None,
        checkpoints: PostgresCheckpointService | None = None,
        guest_outputs: GuestOutputPort | None = None,
        pi_port: PiInvocationPort | None = None,
        guest_model_id: str = "qwen3.6-27b",
        guest_ready_timeout_seconds: int = 45,
        telemetry: SafeTelemetry | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._registry = registry
        self._invocation = AgentInvocationService(registry, gateway, artifacts)
        self._attempts = PostgresAgentAttemptStore(unit_of_work, registry)
        self._artifacts = artifacts
        self._events = DurableEventService(unit_of_work, notifier)
        self._acceptance = PreDeliveryStageService()
        self._approval_requests = PostgresApprovalService(unit_of_work)
        self._lifecycle = lifecycle
        self._imports = imports
        self._checkpoints = checkpoints
        self._guest_outputs = guest_outputs
        self._pi_port = pi_port
        self._guest_model_id = guest_model_id
        self._telemetry = telemetry or SafeTelemetry(NoopTelemetrySink())
        if not 1 <= guest_ready_timeout_seconds <= 300:
            raise ValueError("guest_ready_timeout_seconds is outside range")
        self._guest_ready_timeout_seconds = guest_ready_timeout_seconds

    def evaluate(self, *, run_id: str, stage: ControlStage) -> StageResult:
        started_at = time.monotonic()
        context = self._run_context(run_id)
        self._require_agent_input(run_id)
        if context.run.current_gate != stage:
            raise StageApplicationError("stage_not_authoritative_gate")
        handlers: dict[ControlStage, Callable[[_RunContext], StageResult]] = {
            "INTAKE": self._intake,
            "INVESTIGATE": self._investigate,
            "DESIGN": self._design,
            "DESIGN_CRITIQUE": self._design_critique,
            "PLAN": self._plan,
            "VALIDATE_PLAN": self._validate_plan,
            "DISPATCH": self._dispatch,
            "EXECUTE": self._execute,
            "LOCAL_VERIFY": self._local_verify,
            "INTEGRATE": self._integrate,
            "OUTCOME_VERIFY": self._outcome_verify,
            "TRIAGE": self._triage,
            "USER_APPROVAL": self._user_approval,
            "RESUME_GATE": self._resume_gate,
        }
        try:
            handler = handlers[stage]
        except KeyError as error:
            raise StageApplicationError(
                f"production_stage_not_implemented:{stage}"
            ) from error
        try:
            result = handler(context)
        except Exception as error:
            self._telemetry.span(
                "runner.stage",
                run_id=run_id,
                stage=stage,
                status="failed",
                error_code=type(error).__name__,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            raise
        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._telemetry.span(
            "runner.stage",
            run_id=run_id,
            stage=stage,
            status="completed",
            outcome=result.status,
            duration_ms=duration_ms,
        )
        self._telemetry.metric(
            "orchestrator.runner.stage.duration",
            float(duration_ms),
            stage=stage,
            outcome=result.status,
        )
        return result

    def _require_agent_input(self, run_id: str) -> None:
        with self._unit_of_work.transaction() as unit_of_work:
            owner = unit_of_work.connection.execute(
                text(
                    "SELECT owner FROM workspace_input_ownership WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar()
        if owner in {"PAUSED", "USER"}:
            raise AutomationPausedError(f"guest automation paused for {run_id}")

    def _intake(self, context: _RunContext) -> StageResult:
        expected_charter_id = self._identifier("charter", context.run.run_id)
        proposal = self._invoke(
            context,
            stage="INTAKE",
            result_type=CharterProposal,
            design_version=1,
            payload={
                "user_request": context.run.outcome,
                "project_id": context.project_id,
                "source_fingerprint": context.source_fingerprint,
                "expected_charter_id": expected_charter_id,
            },
        )
        if proposal.authority_questions:
            raise StageApplicationError("charter_authority_questions_require_approval")
        timestamp = datetime.now(UTC)
        key = f"stage:accept-charter:{context.run.run_id}"
        charter = self._acceptance.accept_charter(
            proposal,
            charter_id=expected_charter_id,
            accepted_by=AuthenticatedActor(
                actor_id=context.user_id,
                kind="human",
                role="request-owner",
                authenticated_at=timestamp,
                authentication_context="development-identity-boundary",
            ),
            metadata=self._metadata(timestamp, key),
        )
        self._apply_event(
            context,
            stage="INTAKE",
            attempt_id=proposal.context.attempt_id,
            event_type="validation.accepted",
            status="accepted",
            summary="Intake charter accepted",
            key=key,
            state_change=lambda unit_of_work: unit_of_work.charters.add(charter),
            inline_detail={"validation_status": "accepted"},
        )
        return StageResult("accepted")

    def _investigate(self, context: _RunContext) -> StageResult:
        charter = self._charter(context.run.run_id)
        self._invoke(
            context,
            stage="INVESTIGATE",
            result_type=InvestigationReport,
            design_version=1,
            payload={"charter": charter.model_dump(mode="json")},
        )
        return StageResult("accepted")

    def _design(self, context: _RunContext) -> StageResult:
        charter = self._charter(context.run.run_id)
        investigation = self._latest_result(
            context.run.run_id, "investigator", InvestigationReport
        )
        current_version = self._current_design_version(context.run.run_id)
        proposed_version = max(1, current_version + 1)
        expected_artifact_id = self._identifier(
            "art_design", f"{context.run.run_id}:{proposed_version}"
        )
        proposal = self._invoke(
            context,
            stage="DESIGN",
            result_type=DesignProposal,
            design_version=max(1, current_version),
            payload={
                "charter": charter.model_dump(mode="json"),
                "investigation": investigation.model_dump(mode="json"),
                "current_design_version": current_version,
                "required_proposed_design_version": proposed_version,
                "required_design_artifact_id": expected_artifact_id,
            },
        )
        if (
            proposal.proposed_design_version != proposed_version
            or proposal.design_artifact_id != expected_artifact_id
        ):
            raise StageApplicationError("design_identity_not_deterministic")
        return StageResult("accepted")

    def _design_critique(self, context: _RunContext) -> StageResult:
        proposal = self._latest_result(
            context.run.run_id, "design-authority", DesignProposal
        )
        charter = self._charter(context.run.run_id)
        critique = self._invoke(
            context,
            stage="DESIGN_CRITIQUE",
            result_type=DesignCritiqueReport,
            design_version=proposal.proposed_design_version,
            payload={
                "charter": charter.model_dump(mode="json"),
                "design_proposal": proposal.model_dump(mode="json"),
            },
        )
        if critique.verdict == "revision":
            return StageResult("revision")
        if critique.verdict == "blocked":
            raise StageApplicationError("design_critique_blocked")

        current_version = self._current_design_version(context.run.run_id)
        timestamp = datetime.now(UTC)
        artifact_version = self._ensure_design_artifact(context, proposal)
        key = (
            f"stage:accept-design:{context.run.run_id}:"
            f"{proposal.proposed_design_version}"
        )
        design = self._acceptance.accept_critic_approved_design(
            proposal,
            critique,
            current_design_version=current_version,
            design_revision_id=self._identifier(
                "design", f"{context.run.run_id}:{proposal.proposed_design_version}"
            ),
            accepted_by=AuthenticatedActor(
                actor_id="service_design_acceptance",
                kind="service",
                role="design-acceptance",
                authenticated_at=timestamp,
                authentication_context="critic-validation-boundary",
            ),
            metadata=self._metadata(timestamp, key + ":revision"),
        )
        artifact = ArtifactRecord(
            artifact_id=proposal.design_artifact_id,
            run_id=context.run.run_id,
            logical_name=f"design-v{proposal.proposed_design_version}",
            version=artifact_version.version,
            media_type=artifact_version.media_type,
            storage_locator=artifact_version.storage_key,
            sha256=artifact_version.content_sha256,
            producer=proposal.context.producer,
            access_policy=("operator", "design-critic", "work-planner"),
            metadata=self._metadata(timestamp, key + ":artifact"),
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            unit_of_work.artifacts.add(artifact)
            unit_of_work.design_revisions.add(design)

        self._apply_event(
            context,
            stage="DESIGN_CRITIQUE",
            attempt_id=critique.context.submission.attempt_id,
            event_type="design.revised",
            status="accepted",
            summary="Independent critique accepted design revision",
            key=key,
            state_change=persist,
            design_version=proposal.proposed_design_version,
            inline_detail={
                "validation_status": "accepted",
                "artifact_version": artifact.version,
            },
        )
        return StageResult("accepted")

    def _plan(self, context: _RunContext) -> StageResult:
        charter = self._charter(context.run.run_id)
        design = self._design_revision(context.run.run_id)
        proposal = self._latest_result(
            context.run.run_id, "design-authority", DesignProposal
        )
        self._invoke(
            context,
            stage="PLAN",
            result_type=ProposedWorkPlan,
            design_version=design.design_version,
            payload={
                "charter": charter.model_dump(mode="json"),
                "design_revision": design.model_dump(mode="json"),
                "design_content": proposal.design_content,
            },
        )
        return StageResult("accepted")

    def _validate_plan(self, context: _RunContext) -> StageResult:
        proposal = self._latest_result(
            context.run.run_id, "work-planner", ProposedWorkPlan
        )
        charter = self._charter(context.run.run_id)
        timestamp = datetime.now(UTC)
        validated = self._acceptance.accept_plan(
            proposal,
            charter_criterion_ids=tuple(
                criterion.criterion_id for criterion in charter.acceptance_criteria
            ),
            protected_artifacts=tuple(charter.protected_artifacts),
            metadata=self._metadata(timestamp, "temporary-plan-metadata"),
        )
        attempt_id = proposal.context.attempt_id
        if not isinstance(validated, AcceptedPlan):
            key = f"stage:reject-plan:{context.run.run_id}:{attempt_id}"
            self._apply_event(
                context,
                stage="VALIDATE_PLAN",
                attempt_id=attempt_id,
                event_type="validation.rejected",
                status="rejected",
                summary="Work plan rejected by deterministic validation",
                key=key,
                state_change=lambda _: None,
                design_version=proposal.context.design_version,
                inline_detail={
                    "validation_status": "rejected",
                    "policy_rule_ids": [
                        rejection.rule_id for rejection in validated.rejections[:16]
                    ],
                },
            )
            return StageResult("rejected")

        key = f"stage:accept-plan:{context.run.run_id}:{proposal.context.proposal_id}"
        nodes = tuple(
            node.model_copy(
                update={
                    "metadata": self._metadata(
                        timestamp, f"{key}:node:{node.work_node_id}"
                    )
                }
            )
            for node in validated.nodes
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            for node in nodes:
                unit_of_work.work_nodes.add(node)
            for node in nodes:
                for dependency in node.depends_on:
                    unit_of_work.connection.execute(
                        text(
                            "INSERT INTO work_edges (edge_id, run_id, from_work_node_id, "
                            "to_work_node_id, edge_type) VALUES ("
                            ":edge_id, :run_id, :source, :target, 'depends_on') "
                            "ON CONFLICT (run_id, from_work_node_id, to_work_node_id, edge_type) "
                            "DO NOTHING"
                        ),
                        {
                            "edge_id": self._identifier(
                                "edge",
                                f"{context.run.run_id}:{dependency}:{node.work_node_id}",
                            ),
                            "run_id": context.run.run_id,
                            "source": dependency,
                            "target": node.work_node_id,
                        },
                    )

        self._apply_event(
            context,
            stage="VALIDATE_PLAN",
            attempt_id=attempt_id,
            event_type="validation.accepted",
            status="accepted",
            summary="Work plan accepted into authoritative work graph",
            key=key,
            state_change=persist,
            design_version=proposal.context.design_version,
            inline_detail={"validation_status": "accepted"},
        )
        return StageResult("accepted")

    def _dispatch(self, context: _RunContext) -> StageResult:
        workspace = self._workspace(context)
        active = self._active_packet(context.run.run_id)
        if active is not None:
            return StageResult("accepted")
        nodes = self._work_nodes(context.run.run_id)
        by_id = {node.work_node_id: node for node in nodes}
        candidates = tuple(
            sorted(
                (
                    node
                    for node in nodes
                    if node.node_type == "LEAF_TASK"
                    and node.status == "READY"
                    and all(
                        dependency in by_id and by_id[dependency].status == "VERIFIED"
                        for dependency in node.depends_on
                    )
                ),
                key=lambda node: node.work_node_id,
            )
        )
        if not candidates:
            raise StageApplicationError("no_dependency_ready_leaf")
        node = candidates[0]
        charter = self._charter(context.run.run_id)
        design = self._design_revision(context.run.run_id)
        plan = self._latest_result(context.run.run_id, "work-planner", ProposedWorkPlan)
        proposal_node = next(
            (item for item in plan.nodes if item.work_node_id == node.work_node_id),
            None,
        )
        if proposal_node is None:
            raise StageApplicationError("accepted_node_missing_plan_source")
        ordinal = self._stage_ordinal(context.run.run_id, "DISPATCH")
        packet_id = self._identifier(
            "pkt", f"{context.run.run_id}:{node.work_node_id}:{ordinal}"
        )
        timestamp = datetime.now(UTC)
        criteria = {
            criterion.criterion_id: criterion
            for criterion in charter.acceptance_criteria
        }
        packet = PacketRecord(
            packet_id=packet_id,
            run_id=context.run.run_id,
            task_id=self._identifier("task", packet_id),
            work_node_id=node.work_node_id,
            task_type=node.node_type,
            goal=node.goal,
            design_baseline=node.design_refs,
            acceptance_criteria=tuple(
                PacketAcceptanceCriterion(
                    criterion_id=criterion_id,
                    observable_result=criteria[criterion_id].description,
                )
                for criterion_id in node.acceptance_criterion_ids
                if criterion_id in criteria
            ),
            input_artifacts=node.inputs,
            output_artifacts=tuple(
                OutputArtifactSpecification(
                    artifact_id=self._identifier(
                        "art", f"{packet_id}:{index}:{description}"
                    ),
                    required_form=description,
                )
                for index, description in enumerate(node.outputs)
            ),
            interfaces=node.interfaces,
            starting_points=tuple(proposal_node.expected_touch_points),
            depends_on=node.depends_on,
            expected_touch_points=proposal_node.expected_touch_points,
            protected_touch_points=charter.protected_artifacts,
            acceptance_checks=tuple(
                AcceptanceCheck(
                    method="inspection",
                    procedure=f"Verify {criteria[criterion_id].description}",
                    evidence=criteria[criterion_id].evidence_expectation,
                )
                for criterion_id in node.acceptance_criterion_ids
                if criterion_id in criteria
            ),
            authority_limits=(
                "Change only expected touch points in the disposable guest",
                "Do not change the accepted design or grant approvals",
            ),
            known_facts=(f"workspace_id={workspace.workspace_id}",),
            issue_contract=IssueContract(
                report_evidence="Report observed and expected behavior with bounded evidence",
                proposed_classifications=(
                    "LOCAL_DEFECT",
                    "INTERFACE_MISMATCH",
                    "DESIGN_GAP",
                    "REQUIREMENT_GAP",
                    "EVIDENCE_GAP",
                    "ENVIRONMENT_BLOCKER",
                ),
            ),
            output_contract=OutputContract(
                status="implemented, blocked, or failed",
                outputs="List only produced artifact identifiers",
                checks="List checks actually executed",
                risks="List remaining risks",
                issues="List unresolved issues",
                design_version_used=design.design_version,
            ),
            metadata=self._metadata(timestamp, f"dispatch:{packet_id}"),
        )
        key = f"stage:dispatch:{packet_id}"

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            unit_of_work.packets.add(packet)
            current = unit_of_work.work_nodes.get(node.work_node_id)
            if current is None or current.status != "READY":
                raise StageApplicationError("dispatch_node_state_changed")
            unit_of_work.work_nodes.compare_and_swap(
                self._node_status(current, "IN_PROGRESS", key),
                expected_record_version=current.metadata.record_version,
            )

        self._apply_event(
            context,
            stage="DISPATCH",
            attempt_id=self._identifier("attempt", packet_id),
            event_type="work_node.started",
            status="accepted",
            summary=f"Dispatched immutable packet for {node.work_node_id}",
            key=key,
            state_change=persist,
            design_version=design.design_version,
            work_node_id=node.work_node_id,
        )
        return StageResult("accepted")

    def _execute(self, context: _RunContext) -> StageResult:
        packet = self._require_latest_packet(context.run.run_id)
        node = self._require_work_node(packet.work_node_id)
        if node.status in {"IMPLEMENTED", "CHANGE_REQUESTED"}:
            return StageResult("accepted")
        if node.status != "IN_PROGRESS":
            raise StageApplicationError("active_packet_not_found")
        existing = self._attempt_for_packet(
            context, "executor", WorkReport, packet.packet_id
        )
        if existing is None:
            workspace, gateway = self._guest_gateway(context)
            report = self._invoke(
                context,
                stage="EXECUTE",
                result_type=WorkReport,
                design_version=packet.output_contract.design_version_used,
                payload={
                    "packet": packet.model_dump(mode="json"),
                    "workspace": {
                        "workspace_id": workspace.workspace_id,
                        "guest_path": workspace.guest_path,
                    },
                },
                invocation_service=AgentInvocationService(
                    self._registry, gateway, self._artifacts
                ),
                work_node_id=packet.work_node_id,
                attempt_scope=packet.packet_id,
            )
        else:
            report = existing
        key = f"stage:execute:{packet.packet_id}:{report.context.report_id}"
        accepted_artifacts: tuple[ArtifactRecord, ...] = ()
        if report.status == "implemented":
            accepted_artifacts = self._intake_work_artifacts(
                context, packet, self._workspace(context), report
            )
        target_status: WorkNodeStatus = (
            "IMPLEMENTED"
            if report.status == "implemented" and accepted_artifacts
            else "CHANGE_REQUESTED"
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            for artifact in accepted_artifacts:
                if unit_of_work.artifacts.get(artifact.artifact_id) is None:
                    unit_of_work.artifacts.add(artifact)
            if accepted_artifacts:
                unit_of_work.connection.execute(
                    text(
                        "UPDATE agent_attempts SET result_artifact_id = :artifact_id "
                        "WHERE attempt_id = :attempt_id AND "
                        "(result_artifact_id IS NULL OR result_artifact_id = :artifact_id)"
                    ),
                    {
                        "attempt_id": report.context.submission.attempt_id,
                        "artifact_id": accepted_artifacts[0].artifact_id,
                    },
                )
            current = unit_of_work.work_nodes.get(node.work_node_id)
            if current is None:
                raise StageApplicationError("work_node_not_found")
            if current.status == target_status:
                return
            if current.status != "IN_PROGRESS":
                raise StageApplicationError("execute_node_state_changed")
            unit_of_work.work_nodes.compare_and_swap(
                self._node_status(current, target_status, key),
                expected_record_version=current.metadata.record_version,
            )

        self._apply_event(
            context,
            stage="EXECUTE",
            attempt_id=report.context.submission.attempt_id,
            event_type="stage.exited",
            status="accepted" if target_status == "IMPLEMENTED" else "failed",
            summary=(
                "Executor output copied out and accepted as immutable artifacts"
                if target_status == "IMPLEMENTED"
                else "Executor output failed artifact or scope intake"
            ),
            key=key,
            state_change=persist,
            design_version=packet.output_contract.design_version_used,
            work_node_id=packet.work_node_id,
        )
        return StageResult("accepted")

    def _local_verify(self, context: _RunContext) -> StageResult:
        packet = self._require_latest_packet(context.run.run_id)
        node = self._require_work_node(packet.work_node_id)
        if node.status == "LOCALLY_VERIFIED":
            return StageResult("pass")
        if node.status != "IMPLEMENTED":
            return StageResult("fail")
        workspace, gateway = self._guest_gateway(context)
        work_report = self._require_packet_attempt(
            context, "executor", WorkReport, packet.packet_id
        )
        output_references = tuple(
            ArtifactReference(artifact_id=item.artifact_id, version=1)
            for item in packet.output_artifacts
            if item.artifact_id is not None
        )
        report = self._invoke(
            context,
            stage="LOCAL_VERIFY",
            result_type=VerificationReport,
            design_version=packet.output_contract.design_version_used,
            payload={
                "packet": packet.model_dump(mode="json"),
                "executor_report": work_report.model_dump(mode="json"),
                "independence_rule": "Do not accept worker claims without direct evidence",
            },
            invocation_service=AgentInvocationService(
                self._registry, gateway, self._artifacts
            ),
            work_node_id=packet.work_node_id,
            attempt_scope=packet.packet_id,
            input_artifacts=output_references,
        )
        expected = {item.criterion_id for item in packet.acceptance_criteria}
        passed = {
            item.criterion_id
            for item in report.criterion_results
            if item.result == "passed"
        }
        accepted = report.verdict == "passed" and passed == expected
        key = f"stage:local-verify:{packet.packet_id}:{report.context.report_id}"
        evidence = tuple(
            EvidenceRecord(
                evidence_id=self._identifier(
                    "evidence", f"{packet.packet_id}:{item.criterion_id}:local"
                ),
                run_id=context.run.run_id,
                work_node_id=packet.work_node_id,
                criterion_id=item.criterion_id,
                result=item.result,
                summary=item.summary,
                supporting_artifacts=tuple(
                    ArtifactPointer(
                        artifact_id=reference.artifact_id,
                        version=reference.version,
                        purpose="independently verified executor output",
                    )
                    for reference in output_references
                ),
                verifier=report.context.submission.producer,
                design_version=packet.output_contract.design_version_used,
                metadata=self._metadata(
                    datetime.now(UTC),
                    f"{key}:evidence:{item.criterion_id}",
                ),
            )
            for item in report.criterion_results
            if item.criterion_id in expected
        )
        if accepted:
            checkpoints = self._require_checkpoints()
            checkpoints.create(
                workspace_id=workspace.workspace_id,
                checkpoint_id=self._identifier("checkpoint", packet.packet_id),
                kind="service_accepted",
                design_version=packet.output_contract.design_version_used,
                work_node_id=packet.work_node_id,
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            for item in evidence:
                if unit_of_work.evidence.get(item.evidence_id) is None:
                    unit_of_work.evidence.add(item)
            current = unit_of_work.work_nodes.get(packet.work_node_id)
            if current is None:
                raise StageApplicationError("work_node_not_found")
            target: WorkNodeStatus = (
                "LOCALLY_VERIFIED" if accepted else "CHANGE_REQUESTED"
            )
            if current.status == target:
                return
            if current.status != "IMPLEMENTED":
                raise StageApplicationError("verification_node_state_changed")
            unit_of_work.work_nodes.compare_and_swap(
                self._node_status(current, target, key),
                expected_record_version=current.metadata.record_version,
            )

        self._apply_event(
            context,
            stage="LOCAL_VERIFY",
            attempt_id=report.context.submission.attempt_id,
            event_type="evidence.recorded",
            status="accepted" if accepted else "failed",
            summary=(
                "Independent local verification accepted"
                if accepted
                else "Independent local verification rejected"
            ),
            key=key,
            state_change=persist,
            design_version=packet.output_contract.design_version_used,
            inline_detail={"validation_status": "accepted" if accepted else "rejected"},
            work_node_id=packet.work_node_id,
        )
        return StageResult("pass" if accepted else "fail")

    def _integrate(self, context: _RunContext) -> StageResult:
        packet = self._require_latest_packet(context.run.run_id)
        node = self._require_work_node(packet.work_node_id)
        if node.status in {"INTEGRATED", "VERIFIED"}:
            return StageResult("pass")
        if node.status != "LOCALLY_VERIFIED":
            return StageResult("fail")
        _, gateway = self._guest_gateway(context)
        verification = self._require_packet_attempt(
            context, "local-verifier", VerificationReport, packet.packet_id
        )
        report = self._invoke(
            context,
            stage="INTEGRATE",
            result_type=IntegrationReport,
            design_version=packet.output_contract.design_version_used,
            payload={
                "packet": packet.model_dump(mode="json"),
                "local_verification": verification.model_dump(mode="json"),
                "verified_siblings": [
                    item.model_dump(mode="json")
                    for item in self._work_nodes(context.run.run_id)
                    if item.status in {"LOCALLY_VERIFIED", "INTEGRATED", "VERIFIED"}
                ],
            },
            invocation_service=AgentInvocationService(
                self._registry, gateway, self._artifacts
            ),
            work_node_id=packet.work_node_id,
            attempt_scope=packet.packet_id,
            input_artifacts=tuple(
                ArtifactReference(artifact_id=item.artifact_id, version=1)
                for item in packet.output_artifacts
                if item.artifact_id is not None
            ),
        )
        accepted = report.status == "integrated" and not report.issues
        key = f"stage:integrate:{packet.packet_id}:{report.context.report_id}"

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            current = unit_of_work.work_nodes.get(packet.work_node_id)
            if current is None:
                raise StageApplicationError("work_node_not_found")
            target: WorkNodeStatus = "INTEGRATED" if accepted else "CHANGE_REQUESTED"
            if current.status == target:
                return
            if current.status != "LOCALLY_VERIFIED":
                raise StageApplicationError("integration_node_state_changed")
            unit_of_work.work_nodes.compare_and_swap(
                self._node_status(current, target, key),
                expected_record_version=current.metadata.record_version,
            )

        self._apply_event(
            context,
            stage="INTEGRATE",
            attempt_id=report.context.submission.attempt_id,
            event_type="stage.exited",
            status="accepted" if accepted else "failed",
            summary=f"Integration report status: {report.status}",
            key=key,
            state_change=persist,
            design_version=packet.output_contract.design_version_used,
            work_node_id=packet.work_node_id,
        )
        return StageResult("pass" if accepted else "fail")

    def _outcome_verify(self, context: _RunContext) -> StageResult:
        nodes = self._work_nodes(context.run.run_id)
        unfinished = tuple(
            node.work_node_id
            for node in nodes
            if node.node_type == "LEAF_TASK"
            and node.status not in {"INTEGRATED", "VERIFIED"}
        )
        if unfinished:
            self._record_pending_work_event(context, unfinished)
            return StageResult("fail")
        charter = self._charter(context.run.run_id)
        workspace, gateway = self._guest_gateway(context)
        design = self._design_revision(context.run.run_id)
        report = self._invoke(
            context,
            stage="OUTCOME_VERIFY",
            result_type=OutcomeEvidence,
            design_version=design.design_version,
            payload={
                "charter": charter.model_dump(mode="json"),
                "integrated_work_nodes": [
                    node.model_dump(mode="json")
                    for node in nodes
                    if node.node_type == "LEAF_TASK"
                ],
                "workspace": {"workspace_id": workspace.workspace_id},
            },
            invocation_service=AgentInvocationService(
                self._registry, gateway, self._artifacts
            ),
            work_node_id=None,
            attempt_scope=f"outcome:{design.design_version}",
        )
        expected = {criterion.criterion_id for criterion in charter.acceptance_criteria}
        passed = {
            item.criterion_id
            for item in report.criterion_results
            if item.result == "passed"
        }
        accepted = report.verdict == "passed" and passed == expected
        key = f"stage:outcome:{context.run.run_id}:{report.context.report_id}"
        evidence = tuple(
            EvidenceRecord(
                evidence_id=self._identifier(
                    "evidence", f"{context.run.run_id}:{item.criterion_id}:outcome"
                ),
                run_id=context.run.run_id,
                work_node_id=self._criterion_work_node(nodes, item.criterion_id),
                criterion_id=item.criterion_id,
                result=item.result,
                summary=item.summary,
                supporting_artifacts=(),
                verifier=report.context.submission.producer,
                design_version=design.design_version,
                metadata=self._metadata(
                    datetime.now(UTC), f"{key}:evidence:{item.criterion_id}"
                ),
            )
            for item in report.criterion_results
            if item.criterion_id in expected
        )
        completion = RunCompletionRecord(
            completion_id=self._identifier("completion", context.run.run_id),
            run_id=context.run.run_id,
            outcome_evidence_ids=tuple(item.evidence_id for item in evidence),
            completed_at=datetime.now(UTC),
            completed_by=AuthenticatedActor(
                actor_id="service_outcome_acceptance",
                kind="service",
                role="outcome-acceptance",
                authenticated_at=datetime.now(UTC),
                authentication_context="independent-verification-boundary",
            ),
            authority=AuthorityGrant(
                scope="completion",
                source="outcome-verification",
                granted_at=datetime.now(UTC),
            ),
            summary="All charter criteria passed independent outcome verification",
            metadata=self._metadata(datetime.now(UTC), f"{key}:completion"),
        )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            for item in evidence:
                if unit_of_work.evidence.get(item.evidence_id) is None:
                    unit_of_work.evidence.add(item)
            if accepted:
                for original in nodes:
                    if (
                        original.node_type != "LEAF_TASK"
                        or original.status == "VERIFIED"
                    ):
                        continue
                    current = unit_of_work.work_nodes.get(original.work_node_id)
                    if current is None or current.status != "INTEGRATED":
                        raise StageApplicationError("outcome_node_state_changed")
                    unit_of_work.work_nodes.compare_and_swap(
                        self._node_status(current, "VERIFIED", key),
                        expected_record_version=current.metadata.record_version,
                    )
                if unit_of_work.run_completions.get(completion.completion_id) is None:
                    unit_of_work.run_completions.add(completion)

        self._apply_event(
            context,
            stage="OUTCOME_VERIFY",
            attempt_id=report.context.submission.attempt_id,
            event_type="validation.accepted" if accepted else "validation.rejected",
            status="accepted" if accepted else "failed",
            summary=(
                "Independent outcome verification accepted"
                if accepted
                else "Independent outcome verification rejected"
            ),
            key=key,
            state_change=persist,
            design_version=design.design_version,
            inline_detail={"validation_status": "accepted" if accepted else "rejected"},
        )
        return StageResult("pass" if accepted else "fail")

    def _triage(self, context: _RunContext) -> StageResult:
        nodes = self._work_nodes(context.run.run_id)
        if any(node.status == "READY" for node in nodes):
            return StageResult("local_defect")
        packet = self._require_latest_packet(context.run.run_id)
        design = self._design_revision(context.run.run_id)
        issue = self._invoke(
            context,
            stage="TRIAGE",
            result_type=IssueReport,
            design_version=design.design_version,
            payload={
                "packet": packet.model_dump(mode="json"),
                "work_node": self._require_work_node(packet.work_node_id).model_dump(
                    mode="json"
                ),
                "executor_report": self._optional_packet_attempt_payload(
                    context, "executor", WorkReport, packet.packet_id
                ),
                "verification_report": self._optional_packet_attempt_payload(
                    context,
                    "local-verifier",
                    VerificationReport,
                    packet.packet_id,
                ),
                "integration_report": self._optional_packet_attempt_payload(
                    context, "integrator", IntegrationReport, packet.packet_id
                ),
            },
            work_node_id=packet.work_node_id,
            attempt_scope=packet.packet_id,
        )
        route = route_issue(issue.proposed_classification)
        status_value: StageStatus = (
            "design_gap"
            if route == "DESIGN_REVISION"
            else "authority_needed"
            if route == "USER_APPROVAL"
            else "cannot_continue"
            if route == "BLOCKED"
            else "local_defect"
        )
        key = f"stage:triage:{packet.packet_id}:{issue.context.report_id}"
        record = IssueRecord(
            issue_id=self._identifier("issue", issue.context.report_id),
            run_id=context.run.run_id,
            reporter=issue.context.submission.producer,
            affected_work_node_ids=issue.affected_work_node_ids,
            observed_evidence=issue.observed_evidence,
            expected_result=issue.expected_result,
            actual_result=issue.actual_result,
            classification=issue.proposed_classification,
            severity=issue.severity,
            blocking=issue.blocking,
            design_version=design.design_version,
            routing_outcome=route,
            metadata=self._metadata(datetime.now(UTC), key),
        )
        if status_value == "authority_needed":
            self._approval_requests.request(
                run_id=context.run.run_id,
                authority="design"
                if issue.proposed_classification == "REQUIREMENT_GAP"
                else "transition",
                affected_versions=(str(design.design_version),),
                idempotency_key=f"triage:{record.issue_id}",
            )

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            if unit_of_work.issues.get(record.issue_id) is None:
                unit_of_work.issues.add(record)
            if status_value == "local_defect":
                for node_id in issue.affected_work_node_ids:
                    current = unit_of_work.work_nodes.get(node_id)
                    if current is None or current.status == "READY":
                        continue
                    if current.status not in {"CHANGE_REQUESTED", "BLOCKED"}:
                        continue
                    unit_of_work.work_nodes.compare_and_swap(
                        self._node_status(current, "READY", key),
                        expected_record_version=current.metadata.record_version,
                    )

        self._apply_event(
            context,
            stage="TRIAGE",
            attempt_id=issue.context.submission.attempt_id,
            event_type="validation.accepted",
            status="accepted",
            summary=f"Issue routed to {route}",
            key=key,
            state_change=persist,
            design_version=design.design_version,
            inline_detail={"policy_rule_ids": [route.lower().replace("_", "-")]},
        )
        return StageResult(status_value)

    def _user_approval(self, context: _RunContext) -> StageResult:
        with self._unit_of_work.transaction() as unit_of_work:
            decision = unit_of_work.connection.execute(
                text(
                    "SELECT status FROM approval_requests "
                    "WHERE run_id = :run_id ORDER BY requested_at DESC LIMIT 1"
                ),
                {"run_id": context.run.run_id},
            ).scalar()
        if decision not in {"approved", "rejected"}:
            raise StageApplicationError("user_approval_pending")
        return StageResult("approved" if decision == "approved" else "rejected")

    def _resume_gate(self, context: _RunContext) -> StageResult:
        del context
        return StageResult("accepted")

    def _invoke(
        self,
        context: _RunContext,
        *,
        stage: ControlStage,
        result_type: type[ResultT],
        design_version: int,
        payload: dict[str, object],
        invocation_service: AgentInvocationService | None = None,
        work_node_id: str | None = None,
        attempt_scope: str | None = None,
        input_artifacts: tuple[ArtifactReference, ...] = (),
    ) -> ResultT:
        agent_id = _STAGE_AGENTS[stage]
        ordinal = self._stage_ordinal(context.run.run_id, stage)
        attempt_id = self._identifier(
            "attempt",
            f"{context.run.run_id}:{stage}:"
            + (
                attempt_scope
                if attempt_scope is not None
                else f"{ordinal}:{context.queue_attempt}"
            ),
        )
        invocation = InvocationInput(
            agent_id=agent_id,
            run_id=context.run.run_id,
            attempt_id=attempt_id,
            design_version=design_version,
            work_node_id=work_node_id,
            tenant_id=context.run.tenant_id,
            input_artifacts=input_artifacts,
            context_payload=payload,
        )
        key = f"agent:start:{attempt_id}"
        started: StoredAttempt | None = None

        def begin(_: PostgresUnitOfWork) -> None:
            nonlocal started
            started = self._attempts.begin(
                invocation,
                input_context=payload,
                trace_id=context.run.metadata.trace_id
                or self._trace_id(context.run.run_id),
            )

        self._apply_event(
            context,
            stage=stage,
            attempt_id=attempt_id,
            event_type="agent.started",
            status="started",
            summary=f"{agent_id.replace('-', ' ').title()} invocation started",
            key=key,
            state_change=begin,
            design_version=design_version,
            inline_detail={"retry_count": max(0, context.queue_attempt - 1)},
        )
        if started is None:
            started = self._attempts.begin(
                invocation,
                input_context=payload,
                trace_id=context.run.metadata.trace_id
                or self._trace_id(context.run.run_id),
            )
        if started.status == "accepted":
            return self._parse_result(started, result_type)
        if started.status == "rejected":
            raise InvocationRejected(
                started.rejection_code or "rejected_attempt",
                retryable=bool(started.retryable),
            )
        invocation_started = time.monotonic()
        try:
            result = (invocation_service or self._invocation).invoke(invocation)
        except InvocationRejected as error:
            self._telemetry.span(
                "agent.invoke",
                run_id=context.run.run_id,
                stage=stage,
                node_id=agent_id,
                attempt_id=attempt_id,
                status="rejected",
                error_code=error.code,
                duration_ms=int((time.monotonic() - invocation_started) * 1000),
            )
            rejection_code = error.code
            rejection_retryable = error.retryable
            reject_key = f"agent:reject:{attempt_id}:{error.code}"

            def reject_attempt(_: PostgresUnitOfWork) -> None:
                self._attempts.reject(
                    attempt_id,
                    code=rejection_code,
                    retryable=rejection_retryable,
                )

            self._apply_event(
                context,
                stage=stage,
                attempt_id=attempt_id,
                event_type="agent.failed",
                status="failed",
                summary=f"{agent_id.replace('-', ' ').title()} invocation rejected",
                key=reject_key,
                state_change=reject_attempt,
                design_version=design_version,
                inline_detail={"validation_status": "rejected"},
            )
            raise
        invocation_duration_ms = int((time.monotonic() - invocation_started) * 1000)
        token_count = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        self._telemetry.span(
            "agent.invoke",
            run_id=context.run.run_id,
            stage=stage,
            node_id=agent_id,
            attempt_id=attempt_id,
            status="completed",
            token_count=token_count,
            duration_ms=invocation_duration_ms,
        )
        self._telemetry.metric(
            "orchestrator.agent.duration",
            float(invocation_duration_ms),
            stage=stage,
            status="completed",
        )
        accept_key = f"agent:accept:{attempt_id}"

        def accept_attempt(_: PostgresUnitOfWork) -> None:
            self._attempts.accept(attempt_id, result)

        self._apply_event(
            context,
            stage=stage,
            attempt_id=attempt_id,
            event_type="agent.completed",
            status="completed",
            summary=f"{agent_id.replace('-', ' ').title()} returned validated output",
            key=accept_key,
            state_change=accept_attempt,
            design_version=design_version,
            inline_detail={
                "validation_status": "accepted",
                "token_count": token_count,
            },
        )
        return cast(ResultT, result.result)

    def _workspace(self, context: _RunContext) -> WorkspaceImport:
        if self._lifecycle is None or self._imports is None:
            raise StageApplicationError("delivery_workspace_service_not_configured")
        handle = self._lifecycle.create(context.run.run_id)
        deadline = time.monotonic() + self._guest_ready_timeout_seconds
        while handle.status != "ready" and time.monotonic() < deadline:
            handle = self._lifecycle.probe(context.run.run_id)
            if handle.status != "ready":
                time.sleep(0.25)
        if handle.status != "ready":
            raise StageApplicationError("guest_not_ready")
        workspace_id = self._lifecycle.workspace_id(context.run.run_id)
        return self._imports.import_snapshot(
            workspace_id=workspace_id,
            run_id=context.run.run_id,
            project_id=context.project_id,
            expected_source_fingerprint=context.source_fingerprint,
        )

    def _intake_work_artifacts(
        self,
        context: _RunContext,
        packet: PacketRecord,
        workspace: WorkspaceImport,
        report: WorkReport,
    ) -> tuple[ArtifactRecord, ...]:
        raw_expected_ids = tuple(item.artifact_id for item in packet.output_artifacts)
        if any(artifact_id is None for artifact_id in raw_expected_ids):
            return ()
        expected_ids = cast(tuple[str, ...], raw_expected_ids)
        if (
            len(report.output_artifact_ids) != len(expected_ids)
            or set(report.output_artifact_ids) != set(expected_ids)
            or self._guest_outputs is None
        ):
            return ()
        checkpoint = self._require_checkpoints().create(
            workspace_id=workspace.workspace_id,
            checkpoint_id=self._identifier(
                "checkpoint", f"execution:{packet.packet_id}"
            ),
            kind="execution",
            design_version=packet.output_contract.design_version_used,
            work_node_id=packet.work_node_id,
        )
        changed = self._guest_outputs.diff_paths(
            workspace, workspace.baseline.commit_hash, checkpoint.commit_hash
        )
        if not changed or any(
            not self._work_path_allowed(item.path, packet) for item in changed
        ):
            return ()
        patch = self._guest_outputs.export_patch(
            workspace, workspace.baseline.commit_hash, checkpoint.commit_hash
        )
        if not patch or len(patch) > 10_485_760:
            return ()
        timestamp = datetime.now(UTC)
        accepted: list[ArtifactRecord] = []
        for index, artifact_id in enumerate(expected_ids):
            request = ArtifactPublishRequest(
                artifact_id=artifact_id,
                scope=ArtifactScope(
                    tenant_id=context.run.tenant_id,
                    run_id=context.run.run_id,
                    allowed_roles=(
                        "operator",
                        "local-verifier",
                        "integrator",
                        "outcome-verifier",
                    ),
                ),
                media_type="text/x-diff",
                expected_version=0,
                expected_sha256=sha256(patch).hexdigest(),
            )
            try:
                version = self._artifacts.publish(request, patch)
            except ArtifactVersionConflictError:
                stored = self._artifacts.read(
                    ArtifactReference(artifact_id=artifact_id, version=1),
                    ArtifactAccessRequest(
                        tenant_id=context.run.tenant_id,
                        run_id=context.run.run_id,
                        role="local-verifier",
                    ),
                )
                if stored.content != patch:
                    raise StageApplicationError("work_artifact_content_conflict") from None
                version = ArtifactVersionRecord(
                    artifact_id=stored.metadata.artifact_id,
                    version=stored.metadata.version,
                    scope=stored.metadata.scope,
                    media_type=stored.metadata.media_type,
                    content_sha256=stored.metadata.content_sha256,
                    size_bytes=stored.metadata.size_bytes,
                    storage_key=artifact_storage_key(
                        stored.metadata.artifact_id,
                        stored.metadata.version,
                        stored.metadata.content_sha256,
                    ),
                    created_at=stored.metadata.created_at,
                )
            accepted.append(
                ArtifactRecord(
                    artifact_id=artifact_id,
                    run_id=context.run.run_id,
                    work_node_id=packet.work_node_id,
                    logical_name=f"work-output-{index + 1}",
                    version=version.version,
                    media_type=version.media_type,
                    storage_locator=version.storage_key,
                    sha256=version.content_sha256,
                    producer=report.context.submission.producer,
                    access_policy=(
                        "operator",
                        "local-verifier",
                        "integrator",
                        "outcome-verifier",
                    ),
                    metadata=self._metadata(
                        timestamp, f"artifact-intake:{packet.packet_id}:{artifact_id}"
                    ),
                )
            )
        return tuple(accepted)

    @staticmethod
    def _work_path_allowed(path: str, packet: PacketRecord) -> bool:
        if path.startswith("/") or ".." in path.split("/") or path == ".git":
            return False

        def within(candidate: str, root: str) -> bool:
            normalized = root.rstrip("/")
            return candidate == normalized or candidate.startswith(normalized + "/")

        protected = (*packet.protected_touch_points, ".git")
        if any(within(path, root) for root in protected):
            return False
        return any(within(path, root) for root in packet.expected_touch_points)

    def _guest_gateway(
        self, context: _RunContext
    ) -> tuple[WorkspaceImport, GuestPiModelGateway]:
        if self._pi_port is None:
            raise StageApplicationError("pi_guest_runtime_not_configured")
        workspace = self._workspace(context)
        suffix = context.run.run_id.removeprefix("run_")
        guest = GuestHandle(
            context.run.run_id,
            workspace.guest_id,
            f"overlay-{suffix}",
            "ready",
        )
        return workspace, GuestPiModelGateway(
            self._pi_port,
            guest=guest,
            guest_path=workspace.guest_path,
            model_id=self._guest_model_id,
        )

    def _require_checkpoints(self) -> PostgresCheckpointService:
        if self._checkpoints is None:
            raise StageApplicationError("checkpoint_service_not_configured")
        return self._checkpoints

    def _work_nodes(self, run_id: str) -> tuple[WorkNodeRecord, ...]:
        with self._unit_of_work.transaction() as unit_of_work:
            payloads = unit_of_work.connection.execute(
                text(
                    "SELECT payload FROM work_nodes WHERE run_id = :run_id "
                    "ORDER BY work_node_id"
                ),
                {"run_id": run_id},
            ).scalars()
            return tuple(
                WorkNodeRecord.model_validate_json(json.dumps(payload))
                for payload in payloads
            )

    def _require_work_node(self, work_node_id: str) -> WorkNodeRecord:
        with self._unit_of_work.transaction() as unit_of_work:
            node = unit_of_work.work_nodes.get(work_node_id)
        if node is None:
            raise StageApplicationError("work_node_not_found")
        return node

    def _active_packet(self, run_id: str) -> PacketRecord | None:
        with self._unit_of_work.transaction() as unit_of_work:
            payload = unit_of_work.connection.execute(
                text(
                    "SELECT packets.payload FROM packets JOIN work_nodes USING (work_node_id) "
                    "WHERE packets.run_id = :run_id "
                    "AND work_nodes.payload ->> 'status' = 'IN_PROGRESS' "
                    "ORDER BY packets.created_at DESC LIMIT 1"
                ),
                {"run_id": run_id},
            ).scalar()
        return (
            PacketRecord.model_validate_json(json.dumps(payload))
            if payload is not None
            else None
        )

    def _require_active_packet(self, run_id: str) -> PacketRecord:
        packet = self._active_packet(run_id)
        if packet is None:
            raise StageApplicationError("active_packet_not_found")
        return packet

    def _require_latest_packet(self, run_id: str) -> PacketRecord:
        return self._record_for_run("packets", "run_id", run_id, PacketRecord)

    @staticmethod
    def _node_status(
        node: WorkNodeRecord, status: WorkNodeStatus, key: str
    ) -> WorkNodeRecord:
        return node.model_copy(
            update={
                "status": status,
                "metadata": node.metadata.model_copy(
                    update={
                        "record_version": node.metadata.record_version + 1,
                        "updated_at": datetime.now(UTC),
                        "idempotency_key": key,
                    }
                ),
            }
        )

    def _attempt_identifier(self, run_id: str, stage: ControlStage, scope: str) -> str:
        return self._identifier("attempt", f"{run_id}:{stage}:{scope}")

    def _attempt_for_packet(
        self,
        context: _RunContext,
        agent_id: str,
        result_type: type[ResultT],
        scope: str,
    ) -> ResultT | None:
        stages = {
            "executor": "EXECUTE",
            "local-verifier": "LOCAL_VERIFY",
            "integrator": "INTEGRATE",
            "outcome-verifier": "OUTCOME_VERIFY",
            "issue-triager": "TRIAGE",
        }
        stage = cast(ControlStage, stages[agent_id])
        attempt_id = self._attempt_identifier(context.run.run_id, stage, scope)
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT status, result_type, result_payload FROM agent_attempts "
                        "WHERE attempt_id = :attempt_id AND agent_id = :agent_id"
                    ),
                    {"attempt_id": attempt_id, "agent_id": agent_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "accepted":
            return None
        if row["result_type"] != result_type.__name__ or row["result_payload"] is None:
            raise StageApplicationError("accepted_attempt_result_mismatch")
        return result_type.model_validate_json(json.dumps(row["result_payload"]))

    def _require_packet_attempt(
        self,
        context: _RunContext,
        agent_id: str,
        result_type: type[ResultT],
        scope: str,
    ) -> ResultT:
        result = self._attempt_for_packet(context, agent_id, result_type, scope)
        if result is None:
            raise StageApplicationError(f"{result_type.__name__}_not_ready")
        return result

    def _optional_packet_attempt_payload(
        self,
        context: _RunContext,
        agent_id: str,
        result_type: type[ResultT],
        scope: str,
    ) -> dict[str, object] | None:
        result = self._attempt_for_packet(context, agent_id, result_type, scope)
        return result.model_dump(mode="json") if result is not None else None

    def _record_pending_work_event(
        self, context: _RunContext, unfinished: tuple[str, ...]
    ) -> None:
        design = self._design_revision(context.run.run_id)
        key = f"stage:outcome:pending:{context.run.run_id}:{':'.join(unfinished)}"
        self._apply_event(
            context,
            stage="OUTCOME_VERIFY",
            attempt_id=self._identifier("attempt", key),
            event_type="validation.rejected",
            status="failed",
            summary="Outcome verification deferred until all leaf work is integrated",
            key=key,
            state_change=lambda _: None,
            design_version=design.design_version,
            inline_detail={"policy_rule_ids": ["unfinished-work-remains"]},
        )

    @staticmethod
    def _criterion_work_node(
        nodes: tuple[WorkNodeRecord, ...], criterion_id: str
    ) -> str:
        match = next(
            (
                node.work_node_id
                for node in nodes
                if criterion_id in node.acceptance_criterion_ids
            ),
            None,
        )
        if match is None:
            raise StageApplicationError("outcome_criterion_has_no_work_node")
        return match

    def _apply_event(
        self,
        context: _RunContext,
        *,
        stage: ControlStage,
        attempt_id: str,
        event_type: str,
        status: str,
        summary: str,
        key: str,
        state_change: Callable[[PostgresUnitOfWork], None],
        design_version: int = 1,
        inline_detail: dict[str, JsonValue] | None = None,
        work_node_id: str | None = None,
    ) -> None:
        event_id = self._identifier("evt", key)
        self._events.apply(
            EventDraft.model_validate(
                {
                    "event_id": event_id,
                    "run_id": context.run.run_id,
                    "conversation_id": context.conversation_id,
                    "occurred_at": datetime.now(UTC),
                    "type": event_type,
                    "stage": stage,
                    "node_id": "stage-application-service",
                    "work_node_id": work_node_id,
                    "attempt_id": attempt_id,
                    "design_version": design_version,
                    "packet_version": 1,
                    "actor_role": "stage-application-service",
                    "status": status,
                    "outcome": status,
                    "summary": summary,
                    "detail_ref": f"/api/v1/runs/{context.run.run_id}/events/{event_id}/detail",
                    "correlation_id": key,
                    "trace_id": context.run.metadata.trace_id
                    or self._trace_id(context.run.run_id),
                    "span_id": sha256(key.encode()).hexdigest()[:16],
                    "command_idempotency_key": key,
                    "inline_detail": inline_detail,
                }
            ),
            state_change,
        )

    def _ensure_design_artifact(
        self, context: _RunContext, proposal: DesignProposal
    ) -> ArtifactVersionRecord:
        request = ArtifactPublishRequest(
            artifact_id=proposal.design_artifact_id,
            scope=ArtifactScope(
                tenant_id=context.run.tenant_id,
                run_id=context.run.run_id,
                allowed_roles=("operator", "design-critic", "work-planner"),
            ),
            media_type="text/markdown",
            expected_version=0,
        )
        content = proposal.design_content.encode("utf-8")
        try:
            return self._artifacts.publish(request, content)
        except ArtifactVersionConflictError:
            result = self._artifacts.read(
                ArtifactReference(artifact_id=proposal.design_artifact_id, version=1),
                ArtifactAccessRequest(
                    tenant_id=context.run.tenant_id,
                    run_id=context.run.run_id,
                    role="design-critic",
                ),
            )
            if result.content != content:
                raise StageApplicationError(
                    "design_artifact_content_conflict"
                ) from None
            return ArtifactVersionRecord(
                artifact_id=result.metadata.artifact_id,
                version=result.metadata.version,
                scope=result.metadata.scope,
                media_type=result.metadata.media_type,
                content_sha256=result.metadata.content_sha256,
                size_bytes=result.metadata.size_bytes,
                storage_key=artifact_storage_key(
                    result.metadata.artifact_id,
                    result.metadata.version,
                    result.metadata.content_sha256,
                ),
                created_at=result.metadata.created_at,
            )

    def _run_context(self, run_id: str) -> _RunContext:
        with self._unit_of_work.transaction() as unit_of_work:
            row = (
                unit_of_work.connection.execute(
                    text(
                        "SELECT payload, user_id, conversation_id, project_id, "
                        "source_fingerprint, queue.attempt_count FROM runs "
                        "JOIN run_queue AS queue USING (run_id) WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or any(
            row[key] is None
            for key in (
                "user_id",
                "conversation_id",
                "project_id",
                "source_fingerprint",
            )
        ):
            raise StageApplicationError("run_context_not_ready")
        return _RunContext(
            run=RunRecord.model_validate_json(json.dumps(row["payload"])),
            user_id=row["user_id"],
            conversation_id=row["conversation_id"],
            project_id=row["project_id"],
            source_fingerprint=row["source_fingerprint"],
            queue_attempt=row["attempt_count"],
        )

    def _charter(self, run_id: str) -> CharterRecord:
        return self._record_for_run("charters", "run_id", run_id, CharterRecord)

    def _design_revision(self, run_id: str) -> DesignRevision:
        return self._record_for_run(
            "design_revisions",
            "run_id",
            run_id,
            DesignRevision,
            order="design_version DESC",
        )

    def _record_for_run(
        self,
        table: str,
        column: str,
        value: str,
        model_type: type[ResultT],
        *,
        order: str = "created_at DESC",
    ) -> ResultT:
        with self._unit_of_work.transaction() as unit_of_work:
            payload = unit_of_work.connection.execute(
                text(
                    f"SELECT payload FROM {table} WHERE {column} = :value "
                    f"ORDER BY {order} LIMIT 1"
                ),
                {"value": value},
            ).scalar()
        if payload is None:
            raise StageApplicationError(f"{table}_not_ready")
        return model_type.model_validate_json(json.dumps(payload))

    def _latest_result(
        self, run_id: str, agent_id: str, result_type: type[ResultT]
    ) -> ResultT:
        attempt = self._attempts.latest_accepted(
            run_id=run_id, agent_id=agent_id, result_type=result_type.__name__
        )
        if attempt is None:
            raise StageApplicationError(f"{result_type.__name__}_not_ready")
        return self._parse_result(attempt, result_type)

    @staticmethod
    def _parse_result(attempt: StoredAttempt, result_type: type[ResultT]) -> ResultT:
        if attempt.result_payload is None:
            raise StageApplicationError("accepted_attempt_has_no_result")
        return result_type.model_validate_json(json.dumps(attempt.result_payload))

    def _current_design_version(self, run_id: str) -> int:
        with self._unit_of_work.transaction() as unit_of_work:
            value = unit_of_work.connection.execute(
                text(
                    "SELECT COALESCE(MAX(design_version), 0) FROM design_revisions "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
        return int(value)

    def _stage_ordinal(self, run_id: str, stage: ControlStage) -> int:
        with self._unit_of_work.transaction() as unit_of_work:
            value = unit_of_work.connection.execute(
                text(
                    "SELECT count(*) FROM transition_log "
                    "WHERE run_id = :run_id AND next_state = :stage"
                ),
                {"run_id": run_id, "stage": stage},
            ).scalar_one()
        return int(value)

    @staticmethod
    def _metadata(timestamp: datetime, key: str) -> RecordMetadata:
        return RecordMetadata(
            record_version=1,
            created_at=timestamp,
            updated_at=timestamp,
            idempotency_key=key,
            trace_id=sha256(key.encode()).hexdigest()[:32],
        )

    @staticmethod
    def _identifier(prefix: str, value: str) -> str:
        return f"{prefix}_{sha256(value.encode()).hexdigest()[:32]}"

    @staticmethod
    def _trace_id(run_id: str) -> str:
        return sha256(run_id.encode()).hexdigest()[:32]
