"""Deterministic acceptance services for pre-delivery control-graph stages."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.domain.authoritative import DesignRevision, WorkNodeRecord
from orchestrator.domain.primitives import AuthenticatedActor, RecordMetadata
from orchestrator.domain.proposals import DesignProposal, ProposedWorkPlan
from orchestrator.planning import ApprovedWorkPlan, RejectedWorkPlan, validate_work_plan


class StageAcceptanceError(Exception):
    """An untrusted proposal cannot be accepted at the requested fixed stage."""


@dataclass(frozen=True, slots=True)
class AcceptedPlan:
    approved: ApprovedWorkPlan
    nodes: tuple[WorkNodeRecord, ...]


class PreDeliveryStageService:
    """Agents propose; this service is the only authority that accepts state."""

    def accept_design(
        self,
        proposal: DesignProposal,
        *,
        current_design_version: int,
        design_revision_id: str,
        accepted_by: AuthenticatedActor,
        metadata: RecordMetadata,
    ) -> DesignRevision:
        if proposal.context.producer.role != "design-authority":
            raise StageAcceptanceError("design_producer_not_authorized")
        if proposal.proposed_design_version != current_design_version + 1:
            raise StageAcceptanceError("design_version_not_next")
        if accepted_by.kind != "human":
            raise StageAcceptanceError("design_requires_human_acceptance")
        return DesignRevision(
            design_revision_id=design_revision_id,
            run_id=proposal.context.run_id,
            design_version=proposal.proposed_design_version,
            design_artifact_id=proposal.design_artifact_id,
            decisions=(),
            accepted_by=accepted_by,
            metadata=metadata,
        )

    def accept_plan(
        self,
        proposal: ProposedWorkPlan,
        *,
        charter_criterion_ids: tuple[str, ...],
        protected_artifacts: tuple[str, ...],
        metadata: RecordMetadata,
    ) -> AcceptedPlan | RejectedWorkPlan:
        if proposal.context.producer.role != "work-planner":
            raise StageAcceptanceError("plan_producer_not_authorized")
        validated = validate_work_plan(
            proposal,
            charter_criterion_ids=charter_criterion_ids,
            protected_artifacts=protected_artifacts,
        )
        if isinstance(validated, RejectedWorkPlan):
            return validated
        nodes = tuple(
            WorkNodeRecord(
                work_node_id=node.work_node_id,
                run_id=proposal.context.run_id,
                parent_id=node.parent_id,
                node_type=node.node_type,
                goal=node.goal,
                owner_role=node.owner_role,
                status="READY" if node.disposition == "READY" else "DESIGNED",
                design_refs=node.design_refs,
                depends_on=node.depends_on,
                inputs=(),
                outputs=node.expected_outputs,
                interfaces=node.interfaces,
                acceptance_criterion_ids=node.acceptance_criterion_ids,
                child_ids=node.child_ids,
                metadata=metadata,
            )
            for node in validated.nodes
        )
        return AcceptedPlan(validated, nodes)
