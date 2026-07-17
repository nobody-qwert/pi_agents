"""Triage policies are pure and never permit a free-form route."""

from __future__ import annotations

from orchestrator.triage import route_issue


def test_every_issue_class_has_one_fixed_route() -> None:
    assert route_issue("LOCAL_DEFECT") == "LOCAL_REPAIR"
    assert route_issue("INTERFACE_MISMATCH") == "INTEGRATION_REPAIR"
    assert route_issue("DESIGN_GAP") == "DESIGN_REVISION"
    assert route_issue("REQUIREMENT_GAP") == "USER_APPROVAL"
    assert route_issue("EVIDENCE_GAP") == "EVIDENCE_REVIEW"
    assert route_issue("ENVIRONMENT_BLOCKER") == "BLOCKED"
