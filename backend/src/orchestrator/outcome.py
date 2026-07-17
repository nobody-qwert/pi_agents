"""Integration/outcome gate aggregation and bounded feedback-loop routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from orchestrator.domain.primitives import IssueClassification
from orchestrator.domain.reports import OutcomeEvidence
from orchestrator.triage import IssueRoute, route_issue

TerminalGate = Literal["COMPLETED", "TRIAGE", "USER_APPROVAL", "BLOCKED"]


class OutcomeGateError(Exception):
    """Outcome evidence or loop progression is invalid for the current run."""


@dataclass(frozen=True, slots=True)
class OutcomeDecision:
    gate: TerminalGate
    reason: str
    missing_criteria: tuple[str, ...] = ()
    issue_route: IssueRoute | None = None


class OutcomeLoopService:
    """Completion is possible only after independent evidence and all fixed gates."""

    def __init__(self, *, max_feedback_loops: int = 3) -> None:
        if max_feedback_loops < 1:
            raise ValueError("max_feedback_loops must be positive")
        self._max_feedback_loops = max_feedback_loops
        self._loops: dict[str, int] = {}

    def evaluate(
        self,
        *,
        run_id: str,
        required_criteria: tuple[str, ...],
        outcome: OutcomeEvidence,
        integration_passed: bool,
        blocking_issue: bool,
        mandatory_approvals_satisfied: bool,
    ) -> OutcomeDecision:
        submission = outcome.context.submission
        if (
            submission.run_id != run_id
            or submission.producer.role != "outcome-verifier"
        ):
            raise OutcomeGateError("outcome_evidence_not_independent_or_run_bound")
        passed = {
            result.criterion_id
            for result in outcome.criterion_results
            if result.result == "passed"
        }
        missing = tuple(sorted(set(required_criteria).difference(passed)))
        if blocking_issue:
            return OutcomeDecision("BLOCKED", "blocking_issue")
        if not mandatory_approvals_satisfied:
            return OutcomeDecision("USER_APPROVAL", "mandatory_approval_pending")
        if not integration_passed:
            return OutcomeDecision("TRIAGE", "integration_not_verified")
        if outcome.verdict != "passed" or missing:
            return OutcomeDecision("TRIAGE", "outcome_criteria_not_verified", missing)
        return OutcomeDecision("COMPLETED", "all_outcome_gates_satisfied")

    def feedback_route(self, *, run_id: str, classification: str) -> OutcomeDecision:
        count = self._loops.get(run_id, 0) + 1
        self._loops[run_id] = count
        if count > self._max_feedback_loops:
            return OutcomeDecision("BLOCKED", "feedback_budget_exhausted")
        try:
            route = route_issue(cast(IssueClassification, classification))
        except (KeyError, TypeError) as error:
            raise OutcomeGateError("unknown_issue_classification") from error
        if route == "USER_APPROVAL":
            return OutcomeDecision(
                "USER_APPROVAL", "issue_requires_user_approval", issue_route=route
            )
        if route == "BLOCKED":
            return OutcomeDecision("BLOCKED", "environment_blocker", issue_route=route)
        return OutcomeDecision("TRIAGE", "controlled_feedback", issue_route=route)
