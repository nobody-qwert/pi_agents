"""Independent outcome gate and feedback-loop tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from orchestrator.domain.primitives import AgentActor, CriterionResult
from orchestrator.domain.proposals import SubmissionContext
from orchestrator.domain.reports import OutcomeEvidence, ReportContext
from orchestrator.outcome import OutcomeLoopService


def evidence(
    verdict: Literal["passed", "failed", "inconclusive"],
    results: tuple[CriterionResult, ...],
) -> OutcomeEvidence:
    now = datetime.now(UTC)
    return OutcomeEvidence(
        context=ReportContext(
            report_id="report_outcome",
            submission=SubmissionContext(
                proposal_id="proposal_outcome",
                run_id="run_example",
                attempt_id="attempt_outcome",
                submitted_at=now,
                producer=AgentActor(
                    actor_id="agent_outcome_verifier",
                    kind="agent",
                    role="outcome-verifier",
                ),
                design_version=1,
            ),
            reported_at=now,
        ),
        verdict=verdict,
        criterion_results=results,
        summary="Outcome result",
    )


def test_outcome_requires_all_criteria_integration_and_approvals() -> None:
    service = OutcomeLoopService()
    outcome = evidence(
        "passed",
        (CriterionResult(criterion_id="criterion_one", result="passed", summary="ok"),),
    )
    assert (
        service.evaluate(
            run_id="run_example",
            required_criteria=("criterion_one",),
            outcome=outcome,
            integration_passed=True,
            blocking_issue=False,
            mandatory_approvals_satisfied=True,
        ).gate
        == "COMPLETED"
    )
    assert (
        service.evaluate(
            run_id="run_example",
            required_criteria=("criterion_one", "criterion_two"),
            outcome=outcome,
            integration_passed=True,
            blocking_issue=False,
            mandatory_approvals_satisfied=True,
        ).gate
        == "TRIAGE"
    )
    assert (
        service.evaluate(
            run_id="run_example",
            required_criteria=("criterion_one",),
            outcome=outcome,
            integration_passed=True,
            blocking_issue=False,
            mandatory_approvals_satisfied=False,
        ).gate
        == "USER_APPROVAL"
    )


def test_feedback_routes_are_bounded_and_preserve_issue_taxonomy() -> None:
    service = OutcomeLoopService(max_feedback_loops=1)
    assert (
        service.feedback_route(
            run_id="run_example", classification="LOCAL_DEFECT"
        ).issue_route
        == "LOCAL_REPAIR"
    )
    assert (
        service.feedback_route(run_id="run_example", classification="DESIGN_GAP").gate
        == "BLOCKED"
    )
