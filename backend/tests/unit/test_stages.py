"""Pre-delivery proposal acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orchestrator.domain.primitives import (
    AgentActor,
    AuthenticatedActor,
    RecordMetadata,
)
from orchestrator.domain.proposals import DesignProposal, SubmissionContext
from orchestrator.stages import PreDeliveryStageService, StageAcceptanceError


def design_proposal(role: str, version: int) -> DesignProposal:
    now = datetime.now(UTC)
    return DesignProposal(
        context=SubmissionContext(
            proposal_id="proposal_design",
            run_id="run_example",
            attempt_id="attempt_design",
            submitted_at=now,
            producer=AgentActor(
                actor_id=f"agent_{role.replace('-', '_')}", kind="agent", role=role
            ),
            design_version=1,
        ),
        proposed_design_version=version,
        design_artifact_id="art_design",
        design_content="# Design\n\nA complete proposed design.",
        summary="Design proposal",
    )


def test_design_acceptance_requires_authorized_producer_next_version_and_human() -> (
    None
):
    now = datetime.now(UTC)
    actor = AuthenticatedActor(
        actor_id="user_example",
        kind="human",
        role="owner",
        authenticated_at=now,
        authentication_context="test",
    )
    metadata = RecordMetadata(record_version=1, created_at=now, updated_at=now)
    service = PreDeliveryStageService()
    accepted = service.accept_design(
        design_proposal("design-authority", 2),
        current_design_version=1,
        design_revision_id="design_example",
        accepted_by=actor,
        metadata=metadata,
    )
    assert accepted.design_version == 2
    with pytest.raises(StageAcceptanceError, match="design_producer_not_authorized"):
        service.accept_design(
            design_proposal("executor", 2),
            current_design_version=1,
            design_revision_id="design_other",
            accepted_by=actor,
            metadata=metadata,
        )
    with pytest.raises(StageAcceptanceError, match="design_version_not_next"):
        service.accept_design(
            design_proposal("design-authority", 3),
            current_design_version=1,
            design_revision_id="design_other",
            accepted_by=actor,
            metadata=metadata,
        )
