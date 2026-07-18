"""PostgreSQL proof for the production pre-delivery stage application port."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy import text

from orchestrator.artifacts import (
    ArtifactService,
    LocalVolumeArtifactStore,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import ArtifactPolicy
from orchestrator.checkpoints import (
    LocalGuestCheckpointAdapter,
    PostgresCheckpointService,
)
from orchestrator.commands import PostgresRunCommandService
from orchestrator.domain import (
    AcceptanceCriterion,
    AgentActor,
    CharterProposal,
    ControlStage,
    CriterionResult,
    DesignCritiqueReport,
    DesignProposal,
    DesignReference,
    IntegrationReport,
    InvestigationReport,
    LeafReadinessClaim,
    OutcomeEvidence,
    ProposedWorkPlan,
    ReportContext,
    StrictDomainModel,
    SubmissionContext,
    VerificationReport,
    WorkNodeProposal,
    WorkReport,
)
from orchestrator.graph import load_agent_registry
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.pi_rpc import PiRole, PiRpcResult
from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_preview import LocalGuestPreviewAdapter
from orchestrator.runner import PostgresRunLeaseQueue, RunnerCoordinator
from orchestrator.runner.application_stages import ProductionPreDeliveryStagePort
from orchestrator.vm import GuestHandle, PostgresVmLifecycleService
from orchestrator.workspace import (
    LocalGuestWorkspaceAdapter,
    PostgresWorkspaceImportStore,
    WorkspaceImportService,
)


class NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


class ScriptedGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status="ready", configured_model_id="qwen3.6-27b")

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        del cancellation
        self.calls.append(request.agent_id)
        envelope = json.loads(request.user_prompt)
        context = envelope["context"]
        submission = SubmissionContext(
            proposal_id=f"proposal_{request.agent_id.replace('-', '_')}_stage",
            run_id=envelope["run_id"],
            work_node_id=envelope["work_node_id"],
            attempt_id=envelope["attempt_id"],
            submitted_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
            producer=AgentActor(
                actor_id=f"agent_{request.agent_id.replace('-', '_')}",
                kind="agent",
                role=request.agent_id,
            ),
            design_version=envelope["design_version"],
        )
        result = self._result(request.agent_id, submission, context)
        return ModelResponse(
            content=result.model_dump_json(),
            model_id="qwen3.6-27b",
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=200,
        )

    @staticmethod
    def _result(
        agent_id: str, submission: SubmissionContext, context: dict[str, object]
    ) -> StrictDomainModel:
        if agent_id == "intake":
            return CharterProposal(
                context=submission,
                requested_outcome=str(context["user_request"]),
                intended_users=("operator",),
                included_scope=("durable pre-delivery workflow",),
                excluded_scope=("unapproved host mutation",),
                acceptance_criteria=(
                    AcceptanceCriterion(
                        criterion_id="criterion_delivery",
                        description="The requested outcome is delivered",
                        evidence_expectation="Independent outcome evidence",
                    ),
                ),
                risk_class="low",
                evidence_expectations=("Durable verification evidence",),
            )
        report_context = ReportContext(
            report_id=f"report_{agent_id.replace('-', '_')}_stage",
            submission=submission,
            reported_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
        )
        if agent_id == "investigator":
            return InvestigationReport(
                context=report_context,
                findings=("The selected project is available through policy",),
            )
        if agent_id == "design-authority":
            return DesignProposal(
                context=submission,
                proposed_design_version=int(
                    cast(int, context["required_proposed_design_version"])
                ),
                design_artifact_id=str(context["required_design_artifact_id"]),
                design_content="# Accepted design\n\nUse deterministic boundaries.",
                summary="A bounded deterministic design",
            )
        if agent_id == "design-critic":
            return DesignCritiqueReport(
                context=report_context,
                verdict="accepted",
                summary="The design covers the charter and verification boundary",
            )
        if agent_id == "work-planner":
            return _plan(submission)
        raise AssertionError(f"unexpected agent {agent_id}")


class ReadyVmAdapter:
    def provision(self, run_id: str, guest_id: str, overlay_id: str) -> None:
        del run_id, guest_id, overlay_id

    def probe_ready(self, guest_id: str) -> bool:
        del guest_id
        return True

    def destroy(self, guest_id: str, overlay_id: str) -> None:
        del guest_id, overlay_id


class ScriptedPiPort:
    def __init__(self, guest_root: Path) -> None:
        self._guest_root = guest_root
        self.calls: list[PiRole] = []

    def invoke(
        self, *, guest: GuestHandle, guest_path: str, role: PiRole, prompt: str
    ) -> PiRpcResult:
        self.calls.append(role)
        raw = prompt.partition("<task>\n")[2].rpartition("\n</task>")[0]
        envelope = json.loads(raw)
        context = envelope["context"]
        submission = SubmissionContext(
            proposal_id=f"proposal_{role.replace('-', '_')}_delivery",
            run_id=envelope["run_id"],
            work_node_id=envelope["work_node_id"],
            attempt_id=envelope["attempt_id"],
            submitted_at=datetime(2026, 7, 17, 13, tzinfo=UTC),
            producer=AgentActor(
                actor_id=f"agent_{role.replace('-', '_')}",
                kind="agent",
                role=role,
            ),
            design_version=envelope["design_version"],
        )
        report_context = ReportContext(
            report_id=f"report_{role.replace('-', '_')}_delivery",
            submission=submission,
            reported_at=datetime(2026, 7, 17, 13, tzinfo=UTC),
        )
        if role == "executor":
            packet = context["packet"]
            target = (
                self._guest_root
                / guest.guest_id
                / guest_path
                / packet["expected_touch_points"][0]
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("implemented in guest", encoding="utf-8")
            result: StrictDomainModel = WorkReport(
                context=report_context,
                status="implemented",
                output_artifact_ids=tuple(
                    item["artifact_id"] for item in packet["output_artifacts"]
                ),
                checks=("wrote the declared output",),
            )
        elif role == "local-verifier":
            result = VerificationReport(
                context=report_context,
                work_node_id=cast(str, submission.work_node_id),
                verdict="passed",
                criterion_results=(
                    CriterionResult(
                        criterion_id="criterion_delivery",
                        result="passed",
                        summary="Guest output exists and matches the packet",
                    ),
                ),
                summary="Independent local verification passed",
            )
        elif role == "integrator":
            result = IntegrationReport(
                context=report_context,
                status="integrated",
                interfaces_checked=("delivery",),
            )
        else:
            result = OutcomeEvidence(
                context=report_context,
                verdict="passed",
                criterion_results=(
                    CriterionResult(
                        criterion_id="criterion_delivery",
                        result="passed",
                        summary="The integrated guest result satisfies the charter",
                    ),
                ),
                summary="Every charter criterion has independent evidence",
            )
        return PiRpcResult(text=result.model_dump_json(), tool_events=())


def _claim() -> LeafReadinessClaim:
    return LeafReadinessClaim(
        observable_outcome=True,
        single_responsibility=True,
        inputs_explicit=True,
        outputs_explicit=True,
        design_rules_and_interfaces_cited=True,
        single_verification_boundary=True,
        failure_isolated=True,
        context_fits=True,
        no_unapproved_decisions=True,
    )


def _node(
    node_id: str,
    node_type: str,
    owner: str,
    *,
    parent_id: str | None = "wn_outcome_stage",
    depends_on: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    consumes: tuple[str, ...] = (),
    consumers: tuple[str, ...] = (),
    leaf: bool = True,
) -> WorkNodeProposal:
    return WorkNodeProposal.model_validate(
        {
            "work_node_id": node_id,
            "parent_id": parent_id,
            "node_type": node_type,
            "goal": f"Deliver {node_id}",
            "owner_role": owner,
            "design_refs": (
                DesignReference(design_version=1, section="design", decision_ids=()),
            ),
            "depends_on": depends_on,
            "expected_outputs": (f"output for {node_id}",),
            "output_consumer_ids": consumers,
            "interfaces": tuple(f"Interface {item}" for item in produces + consumes),
            "produces_interfaces": produces,
            "consumes_interfaces": consumes,
            "acceptance_criterion_ids": ("criterion_delivery",),
            "expected_touch_points": (
                (f"output/{node_id}.txt",) if node_type == "LEAF_TASK" else ()
            ),
            "leaf_readiness": _claim() if leaf else None,
        },
        strict=True,
    )


def _plan(submission: SubmissionContext) -> ProposedWorkPlan:
    suffix = submission.run_id.removeprefix("run_")[-8:]
    outcome_id = f"wn_outcome_{suffix}"
    implementation_id = f"wn_implementation_{suffix}"
    integration_id = f"wn_integration_{suffix}"
    verification_id = f"wn_outcome_verification_{suffix}"
    return ProposedWorkPlan(
        context=submission,
        root_work_node_id=outcome_id,
        nodes=(
            _node(
                outcome_id,
                "OUTCOME",
                "coordinator",
                parent_id=None,
                leaf=False,
            ),
            _node(
                implementation_id,
                "LEAF_TASK",
                "executor",
                parent_id=outcome_id,
                produces=("delivery",),
                consumers=(integration_id,),
            ),
            _node(
                integration_id,
                "INTEGRATION",
                "integrator",
                parent_id=outcome_id,
                depends_on=(implementation_id,),
                consumes=("delivery",),
                produces=("integrated",),
                consumers=(verification_id,),
            ),
            _node(
                verification_id,
                "VERIFICATION",
                "outcome-verifier",
                parent_id=outcome_id,
                depends_on=(integration_id,),
                consumes=("integrated",),
            ),
        ),
    )


def test_pre_delivery_stages_are_durable_authoritative_and_restart_idempotent(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    project_root = tmp_path / "projects"
    project = project_root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("stage integration", encoding="utf-8")
    catalog = ProjectCatalog((project_root,))
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    gateway = ScriptedGateway()
    unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
    metadata = PostgresArtifactMetadataRepository(migrated_postgres_database)
    artifacts = ArtifactService(
        content_store=LocalVolumeArtifactStore(tmp_path / "artifacts"),
        metadata_repository=metadata,
        policy=ArtifactPolicy(),
    )
    commands = PostgresRunCommandService(catalog, gateway, unit_of_work)
    queue = PostgresRunLeaseQueue(
        migrated_postgres_database, lease_duration=timedelta(seconds=30)
    )
    stage_port = ProductionPreDeliveryStagePort(
        unit_of_work=unit_of_work,
        registry=registry,
        gateway=gateway,
        artifacts=artifacts,
        notifier=NoopNotifier(),
    )
    coordinator = RunnerCoordinator(unit_of_work, queue)
    try:
        created = commands.create(
            user_id="user_stage",
            project_id=catalog.discover()[0].project_id,
            message="Build the production pre-delivery path",
            idempotency_key="stage-create",
        )
        claim = queue.claim(created.run_id, owner="runner-stage")
        assert claim.lease is not None

        # Repeating evaluation before the coordinator transition simulates a
        # crash between accepted stage state and graph checkpoint persistence.
        assert stage_port.evaluate(run_id=created.run_id, stage="INTAKE").status == (
            "accepted"
        )
        assert stage_port.evaluate(run_id=created.run_id, stage="INTAKE").status == (
            "accepted"
        )
        assert gateway.calls.count("intake") == 1
        coordinator.advance(
            stage="INTAKE",
            result=stage_port.evaluate(run_id=created.run_id, stage="INTAKE"),
            lease=claim.lease,
        )

        for stage in (
            "INVESTIGATE",
            "DESIGN",
            "DESIGN_CRITIQUE",
            "PLAN",
            "VALIDATE_PLAN",
        ):
            result = stage_port.evaluate(run_id=created.run_id, stage=stage)
            coordinator.advance(stage=stage, result=result, lease=claim.lease)

        with unit_of_work.transaction() as transaction:
            counts = transaction.connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM charters WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM design_revisions WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM work_nodes WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM agent_attempts WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM agent_registry_versions)"
                ),
                {"run_id": created.run_id},
            ).one()
            run = transaction.runs.get(created.run_id)

        assert counts == (1, 1, 4, 5, 1)
        assert run is not None and run.current_gate == "DISPATCH"
        assert gateway.calls == [
            "intake",
            "investigator",
            "design-authority",
            "design-critic",
            "work-planner",
        ]
    finally:
        queue.close()
        metadata.close()
        unit_of_work.close()


def test_fixed_graph_delivery_runs_in_guest_and_accepts_independent_evidence(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    project_root = tmp_path / "delivery-projects"
    project = project_root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("delivery integration", encoding="utf-8")
    guest_root = tmp_path / "guest"
    catalog = ProjectCatalog((project_root,))
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    gateway = ScriptedGateway()
    unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
    metadata = PostgresArtifactMetadataRepository(migrated_postgres_database)
    artifacts = ArtifactService(
        content_store=LocalVolumeArtifactStore(tmp_path / "delivery-artifacts"),
        metadata_repository=metadata,
        policy=ArtifactPolicy(),
    )
    commands = PostgresRunCommandService(catalog, gateway, unit_of_work)
    queue = PostgresRunLeaseQueue(
        migrated_postgres_database, lease_duration=timedelta(seconds=120)
    )
    lifecycle = PostgresVmLifecycleService(ReadyVmAdapter(), unit_of_work)
    imports = WorkspaceImportService(
        catalog,
        lifecycle,
        LocalGuestWorkspaceAdapter(guest_root),
        PostgresWorkspaceImportStore(unit_of_work),
    )
    checkpoints = PostgresCheckpointService(
        imports, LocalGuestCheckpointAdapter(guest_root), unit_of_work
    )
    pi_port = ScriptedPiPort(guest_root)
    stage_port = ProductionPreDeliveryStagePort(
        unit_of_work=unit_of_work,
        registry=registry,
        gateway=gateway,
        artifacts=artifacts,
        notifier=NoopNotifier(),
        lifecycle=lifecycle,
        imports=imports,
        checkpoints=checkpoints,
        guest_outputs=LocalGuestPreviewAdapter(guest_root),
        pi_port=pi_port,
    )
    coordinator = RunnerCoordinator(unit_of_work, queue)
    try:
        created = commands.create(
            user_id="user_delivery",
            project_id=catalog.discover()[0].project_id,
            message="Build the complete delivery path",
            idempotency_key="delivery-create",
        )
        claim = queue.claim(created.run_id, owner="runner-delivery")
        assert claim.lease is not None
        expected = {
            "INTAKE": "accepted",
            "INVESTIGATE": "accepted",
            "DESIGN": "accepted",
            "DESIGN_CRITIQUE": "accepted",
            "PLAN": "accepted",
            "VALIDATE_PLAN": "accepted",
            "DISPATCH": "accepted",
            "EXECUTE": "accepted",
            "LOCAL_VERIFY": "pass",
            "INTEGRATE": "pass",
            "OUTCOME_VERIFY": "pass",
        }
        for stage, status_value in expected.items():
            stage_name = cast(ControlStage, stage)
            result = stage_port.evaluate(run_id=created.run_id, stage=stage_name)
            assert result.status == status_value
            coordinator.advance(stage=stage_name, result=result, lease=claim.lease)

        with unit_of_work.transaction() as transaction:
            run = transaction.runs.get(created.run_id)
            node_status = transaction.connection.execute(
                text(
                    "SELECT payload ->> 'status' FROM work_nodes "
                    "WHERE run_id = :run_id AND payload ->> 'node_type' = 'LEAF_TASK'"
                ),
                {"run_id": created.run_id},
            ).scalar_one()
            counts = transaction.connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM packets WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM evidence WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM workspace_checkpoints WHERE run_id = :run_id), "
                    "(SELECT count(*) FROM artifacts WHERE run_id = :run_id "
                    "AND payload ->> 'media_type' = 'text/x-diff'), "
                    "(SELECT count(*) FROM run_completions WHERE run_id = :run_id)"
                ),
                {"run_id": created.run_id},
            ).one()

        assert run is not None and run.status == "completed"
        assert node_status == "VERIFIED"
        assert counts == (1, 2, 3, 1, 1)
        assert pi_port.calls == [
            "executor",
            "local-verifier",
            "integrator",
            "outcome-verifier",
        ]
    finally:
        queue.close()
        metadata.close()
        unit_of_work.close()
