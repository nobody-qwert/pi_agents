"""Code-owned fixed control graph derived from transition policy and registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast, get_args

from orchestrator.domain import RUN_GATE_TRANSITIONS, ControlStage, StrictDomainModel
from orchestrator.domain.primitives import NonEmptyStr, Sha256Digest, ShortStr
from orchestrator.graph.registry import (
    AgentId,
    AgentProjection,
    AgentRegistry,
    project_registry,
)

_NODE_AGENTS: Final[Mapping[ControlStage, str | None]] = MappingProxyType(
    {
        "INTAKE": "intake",
        "INVESTIGATE": "investigator",
        "DESIGN": "design-authority",
        "DESIGN_CRITIQUE": "design-critic",
        "PLAN": "work-planner",
        "VALIDATE_PLAN": None,
        "DISPATCH": None,
        "EXECUTE": "executor",
        "LOCAL_VERIFY": "local-verifier",
        "INTEGRATE": "integrator",
        "OUTCOME_VERIFY": "outcome-verifier",
        "TRIAGE": "issue-triager",
        "USER_APPROVAL": None,
        "RESUME_GATE": None,
        "COMPLETE": None,
        "BLOCKED": None,
    }
)

_NODE_DESCRIPTIONS: Final[Mapping[ControlStage, str]] = MappingProxyType(
    {
        "INTAKE": "Draft the charter and identify user-owned authority questions.",
        "INVESTIGATE": "Establish current ownership, constraints, evidence, and gaps.",
        "DESIGN": "Produce a bounded, versioned design proposal.",
        "DESIGN_CRITIQUE": "Independently critique design coverage and consistency.",
        "PLAN": "Propose a dependency-aware work graph from the accepted design.",
        "VALIDATE_PLAN": "Deterministically validate the proposed work graph.",
        "DISPATCH": "Select approved dependency-ready work without changing topology.",
        "EXECUTE": "Produce one bounded artifact outcome from an immutable packet.",
        "LOCAL_VERIFY": "Independently verify the bounded output.",
        "INTEGRATE": "Assemble verified sibling outputs against their interfaces.",
        "OUTCOME_VERIFY": "Judge integrated evidence against every charter criterion.",
        "TRIAGE": "Classify one issue and select a permitted recovery loop.",
        "USER_APPROVAL": "Pause for an authenticated human authority decision.",
        "RESUME_GATE": "Deterministically restore the permitted execution gate.",
        "COMPLETE": "Terminal successful outcome.",
        "BLOCKED": "Terminal state when the run cannot safely continue.",
    }
)

_CONDITIONS: Final[Mapping[tuple[ControlStage, ControlStage], str | None]] = (
    MappingProxyType(
        {
            ("INTAKE", "INVESTIGATE"): None,
            ("INVESTIGATE", "DESIGN"): None,
            ("DESIGN", "DESIGN_CRITIQUE"): None,
            ("DESIGN_CRITIQUE", "DESIGN"): "revision",
            ("DESIGN_CRITIQUE", "PLAN"): "accepted",
            ("PLAN", "VALIDATE_PLAN"): None,
            ("VALIDATE_PLAN", "DISPATCH"): "accepted",
            ("VALIDATE_PLAN", "TRIAGE"): "rejected",
            ("DISPATCH", "EXECUTE"): None,
            ("EXECUTE", "LOCAL_VERIFY"): None,
            ("LOCAL_VERIFY", "INTEGRATE"): "pass",
            ("LOCAL_VERIFY", "TRIAGE"): "fail",
            ("INTEGRATE", "OUTCOME_VERIFY"): "pass",
            ("INTEGRATE", "TRIAGE"): "fail",
            ("OUTCOME_VERIFY", "COMPLETE"): "pass",
            ("OUTCOME_VERIFY", "TRIAGE"): "fail",
            ("TRIAGE", "DISPATCH"): "local_defect",
            ("TRIAGE", "DESIGN"): "design_gap",
            ("TRIAGE", "USER_APPROVAL"): "authority_needed",
            ("TRIAGE", "BLOCKED"): "cannot_continue",
            ("USER_APPROVAL", "RESUME_GATE"): "approved",
            ("USER_APPROVAL", "BLOCKED"): "rejected",
            ("RESUME_GATE", "DISPATCH"): None,
        }
    )
)


class GraphNodeProjection(StrictDomainModel):
    node_id: ControlStage
    description: NonEmptyStr
    agent_id: AgentId | None
    agent: AgentProjection | None


class GraphEdgeProjection(StrictDomainModel):
    source: ControlStage
    target: ControlStage
    condition: ShortStr | None


class ControlGraphProjection(StrictDomainModel):
    entry_node: ControlStage
    registry_hash: Sha256Digest
    nodes: tuple[GraphNodeProjection, ...]
    edges: tuple[GraphEdgeProjection, ...]


@dataclass(frozen=True, slots=True)
class CompiledControlGraph:
    """Immutable inspection artifact; execution is deliberately a later boundary."""

    projection: ControlGraphProjection

    @property
    def permitted_edges(self) -> frozenset[tuple[ControlStage, ControlStage]]:
        return frozenset((edge.source, edge.target) for edge in self.projection.edges)


def compile_control_graph(registry: AgentRegistry) -> CompiledControlGraph:
    """Compile the code-owned topology with registry-backed node inspection data."""

    stages = cast(tuple[ControlStage, ...], get_args(ControlStage))
    if set(_NODE_AGENTS) != set(stages) or set(_NODE_DESCRIPTIONS) != set(stages):
        raise RuntimeError("fixed graph metadata must cover every ControlStage")

    policy_edges = {
        (source, target)
        for source, targets in RUN_GATE_TRANSITIONS.items()
        for target in targets
    }
    if set(_CONDITIONS) != policy_edges:
        raise RuntimeError(
            "fixed graph conditions must exactly match transition policy"
        )

    registry_projection = project_registry(registry)
    projected_agents = {agent.agent_id: agent for agent in registry_projection.agents}
    assigned = {agent_id for agent_id in _NODE_AGENTS.values() if agent_id is not None}
    if assigned != set(projected_agents):
        raise RuntimeError(
            "fixed graph agent assignments must exactly match the registry"
        )

    nodes_list: list[GraphNodeProjection] = []
    for stage in stages:
        agent_id = _NODE_AGENTS[stage]
        nodes_list.append(
            GraphNodeProjection(
                node_id=stage,
                description=_NODE_DESCRIPTIONS[stage],
                agent_id=agent_id,
                agent=projected_agents[agent_id] if agent_id is not None else None,
            )
        )
    nodes = tuple(nodes_list)
    edges = tuple(
        GraphEdgeProjection(source=source, target=target, condition=condition)
        for (source, target), condition in sorted(_CONDITIONS.items())
    )
    return CompiledControlGraph(
        projection=ControlGraphProjection(
            entry_node="INTAKE",
            registry_hash=registry.registry_hash,
            nodes=nodes,
            edges=edges,
        )
    )
