from pathlib import Path

from orchestrator.domain import RUN_GATE_TRANSITIONS
from orchestrator.graph import compile_control_graph, load_agent_registry

CONFIG_ROOT = Path(__file__).resolve().parents[4] / "config"


def test_compiled_edges_exactly_match_transition_policy() -> None:
    graph = compile_control_graph(load_agent_registry(CONFIG_ROOT))
    policy_edges = frozenset(
        (source, target)
        for source, targets in RUN_GATE_TRANSITIONS.items()
        for target in targets
    )

    assert graph.permitted_edges == policy_edges
    assert len(graph.projection.edges) == len(policy_edges)


def test_graph_projection_uses_the_loaded_registry() -> None:
    registry = load_agent_registry(CONFIG_ROOT)
    graph = compile_control_graph(registry)
    assigned_agents = {
        node.agent_id for node in graph.projection.nodes if node.agent_id is not None
    }

    assert graph.projection.entry_node == "INTAKE"
    assert graph.projection.registry_hash == registry.registry_hash
    assert assigned_agents == set(registry.definitions)
    for node in graph.projection.nodes:
        if node.agent_id is None:
            assert node.agent is None
        else:
            assert node.agent is not None
            assert node.agent.agent_id == node.agent_id
            assert node.agent.config_hash == registry[node.agent_id].config_hash


def test_conditional_status_mapping_is_fixed_and_complete() -> None:
    graph = compile_control_graph(load_agent_registry(CONFIG_ROOT))
    conditions = {
        (edge.source, edge.target): edge.condition for edge in graph.projection.edges
    }

    assert conditions[("DESIGN_CRITIQUE", "PLAN")] == "accepted"
    assert conditions[("DESIGN_CRITIQUE", "DESIGN")] == "revision"
    assert conditions[("LOCAL_VERIFY", "INTEGRATE")] == "pass"
    assert conditions[("LOCAL_VERIFY", "TRIAGE")] == "fail"
    assert conditions[("TRIAGE", "USER_APPROVAL")] == "authority_needed"
    assert conditions[("USER_APPROVAL", "BLOCKED")] == "rejected"
    assert set(conditions) == {
        (source, target)
        for source, targets in RUN_GATE_TRANSITIONS.items()
        for target in targets
    }
