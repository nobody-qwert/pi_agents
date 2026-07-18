"""Deterministic acceptance services for pre-delivery control-graph stages."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.domain.authoritative import (
    CharterRecord,
    DesignRevision,
    WorkNodeRecord,
)
from orchestrator.domain.primitives import AuthenticatedActor, RecordMetadata
from orchestrator.domain.proposals import (
    CharterProposal,
    DesignProposal,
    ProposedWorkPlan,
)
from orchestrator.domain.reports import DesignCritiqueReport
from orchestrator.planning import ApprovedWorkPlan, RejectedWorkPlan, validate_work_plan


class StageAcceptanceError(Exception):
    """An untrusted proposal cannot be accepted at the requested fixed stage."""


@dataclass(frozen=True, slots=True)
class AcceptedPlan:
    approved: ApprovedWorkPlan
    nodes: tuple[WorkNodeRecord, ...]


class PreDeliveryStageService:
    """Agents propose; this service is the only authority that accepts state."""

    def accept_charter(
        self,
        proposal: CharterProposal,
        *,
        charter_id: str,
        accepted_by: AuthenticatedActor,
        metadata: RecordMetadata,
    ) -> CharterRecord:
        if proposal.context.producer.role != "intake":
            raise StageAcceptanceError("charter_producer_not_authorized")
        if accepted_by.kind != "human":
            raise StageAcceptanceError("charter_requires_human_request_owner")
        return CharterRecord(
            charter_id=charter_id,
            run_id=proposal.context.run_id,
            requested_outcome=proposal.requested_outcome,
            intended_users=proposal.intended_users,
            included_scope=proposal.included_scope,
            excluded_scope=proposal.excluded_scope,
            assumptions=proposal.assumptions,
            constraints=proposal.constraints,
            protected_artifacts=proposal.protected_artifacts,
            acceptance_criteria=proposal.acceptance_criteria,
            authority_questions=proposal.authority_questions,
            required_approvals=proposal.required_approvals,
            risk_class=proposal.risk_class,
            evidence_expectations=proposal.evidence_expectations,
            accepted_by=accepted_by,
            metadata=metadata,
        )

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

    def accept_critic_approved_design(
        self,
        proposal: DesignProposal,
        critique: DesignCritiqueReport,
        *,
        current_design_version: int,
        design_revision_id: str,
        accepted_by: AuthenticatedActor,
        metadata: RecordMetadata,
    ) -> DesignRevision:
        if proposal.context.producer.role != "design-authority":
            raise StageAcceptanceError("design_producer_not_authorized")
        if critique.context.submission.producer.role != "design-critic":
            raise StageAcceptanceError("critique_producer_not_authorized")
        if (
            critique.context.submission.run_id != proposal.context.run_id
            or critique.context.submission.design_version
            != proposal.proposed_design_version
        ):
            raise StageAcceptanceError("critique_not_design_bound")
        if critique.verdict != "accepted":
            raise StageAcceptanceError("design_not_recommended_for_acceptance")
        if proposal.proposed_design_version != current_design_version + 1:
            raise StageAcceptanceError("design_version_not_next")
        if accepted_by.kind != "service" or accepted_by.role != "design-acceptance":
            raise StageAcceptanceError("design_acceptance_service_required")
        return DesignRevision(
            design_revision_id=design_revision_id,
            run_id=proposal.context.run_id,
            design_version=proposal.proposed_design_version,
            design_artifact_id=proposal.design_artifact_id,
            decisions=(),
            accepted_by=accepted_by,
            metadata=metadata,
        )
