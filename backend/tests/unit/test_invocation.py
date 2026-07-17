"""Tests for strict agent-result handling without a live model service."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from orchestrator.artifacts import ArtifactService
from orchestrator.domain.primitives import AgentActor
from orchestrator.domain.proposals import SubmissionContext
from orchestrator.domain.reports import ReportContext, WorkReport
from orchestrator.graph import load_agent_registry
from orchestrator.invocation import (
    AgentInvocationService,
    InvocationInput,
    InvocationRejected,
)
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)


class Gateway:
    def __init__(self, content: str) -> None:
        self.content = content

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status="ready", configured_model_id="qwen3.6-27b")

    def complete(
        self, request: ModelRequest, *, cancellation: CancellationToken | None = None
    ) -> ModelResponse:
        return ModelResponse(
            content=self.content, model_id="qwen3.6-27b", finish_reason="stop"
        )


def test_valid_executor_report_is_typed_and_pinned() -> None:
    now = datetime.now(UTC)
    report = WorkReport(
        context=ReportContext(
            report_id="report_result",
            submission=SubmissionContext(
                proposal_id="proposal_result",
                run_id="run_result",
                work_node_id="wn_result",
                attempt_id="attempt_result",
                submitted_at=now,
                producer=AgentActor(
                    actor_id="agent_executor", kind="agent", role="executor"
                ),
                design_version=1,
            ),
            reported_at=now,
        ),
        status="implemented",
    )
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    result = AgentInvocationService(
        registry,
        Gateway(report.model_dump_json()),
        ArtifactService.__new__(ArtifactService),
    ).invoke(
        InvocationInput(
            agent_id="executor",
            run_id="run_result",
            attempt_id="attempt_result",
            design_version=1,
            work_node_id="wn_result",
            tenant_id="tenant_result",
        )
    )
    assert isinstance(result.result, WorkReport)
    assert result.agent_id == "executor"
    assert result.model_id == "qwen3.6-27b"


@pytest.mark.parametrize(
    "content", ["not JSON", '{"kind":"work_report","unexpected":true}']
)
def test_malformed_or_unknown_output_is_rejected(content: str) -> None:
    registry = load_agent_registry(Path(__file__).parents[3] / "config")
    service = AgentInvocationService(
        registry, Gateway(content), ArtifactService.__new__(ArtifactService)
    )
    with pytest.raises(InvocationRejected, match="invalid_structured_output"):
        service.invoke(
            InvocationInput(
                agent_id="executor",
                run_id="run_result",
                attempt_id="attempt_result",
                design_version=1,
                work_node_id="wn_result",
                tenant_id="tenant_result",
            )
        )
