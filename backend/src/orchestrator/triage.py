"""Deterministic issue routes, revision impact analysis, and approval decisions."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from orchestrator.domain.authoritative import WorkNodeRecord
from orchestrator.domain.primitives import IssueClassification

IssueRoute = Literal[
    "LOCAL_REPAIR",
    "INTEGRATION_REPAIR",
    "DESIGN_REVISION",
    "USER_APPROVAL",
    "EVIDENCE_REVIEW",
    "BLOCKED",
]

ISSUE_ROUTES: Final[Mapping[IssueClassification, IssueRoute]] = {
    "LOCAL_DEFECT": "LOCAL_REPAIR",
    "INTERFACE_MISMATCH": "INTEGRATION_REPAIR",
    "DESIGN_GAP": "DESIGN_REVISION",
    "REQUIREMENT_GAP": "USER_APPROVAL",
    "EVIDENCE_GAP": "EVIDENCE_REVIEW",
    "ENVIRONMENT_BLOCKER": "BLOCKED",
}


@dataclass(frozen=True, slots=True)
class Invalidation:
    work_node_id: str
    new_status: Literal["CHANGE_REQUESTED", "INVALIDATED"]


def route_issue(classification: IssueClassification) -> IssueRoute:
    """Route an accepted issue to the smallest fixed control loop."""
    return ISSUE_ROUTES[classification]


def revision_impact(
    nodes: Iterable[WorkNodeRecord],
    *,
    changed_sections: frozenset[str],
    changed_decision_ids: frozenset[str],
) -> tuple[Invalidation, ...]:
    """Invalidate direct design consumers and all dependency descendants only."""
    all_nodes = tuple(nodes)
    direct = {
        node.work_node_id
        for node in all_nodes
        if any(
            reference.section in changed_sections
            or bool(set(reference.decision_ids).intersection(changed_decision_ids))
            for reference in node.design_refs
        )
    }
    children: dict[str, set[str]] = {}
    for node in all_nodes:
        for dependency in node.depends_on:
            children.setdefault(dependency, set()).add(node.work_node_id)
    affected = set(direct)
    queue: deque[str] = deque(sorted(direct))
    while queue:
        for child in sorted(children.get(queue.popleft(), ())):
            if child not in affected:
                affected.add(child)
                queue.append(child)
    by_id = {node.work_node_id: node for node in all_nodes}
    return tuple(
        Invalidation(
            work_node_id=node_id,
            new_status="INVALIDATED"
            if by_id[node_id].status in {"LOCALLY_VERIFIED", "INTEGRATED", "VERIFIED"}
            else "CHANGE_REQUESTED",
        )
        for node_id in sorted(affected)
    )
