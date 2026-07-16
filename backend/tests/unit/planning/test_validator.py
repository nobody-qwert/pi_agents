from __future__ import annotations

from datetime import UTC, datetime
from itertools import permutations
from typing import Any

import pytest

from orchestrator.domain import (
    AgentActor,
    DesignReference,
    LeafReadinessClaim,
    ProposedWorkPlan,
    SubmissionContext,
    WorkNodeProposal,
)
from orchestrator.planning import (
    ApprovedWorkPlan,
    PlanValidationPolicy,
    RejectedWorkPlan,
    evaluate_leaf_readiness,
    validate_work_plan,
)


def ready_claim(**changes: bool) -> LeafReadinessClaim:
    values = {
        "observable_outcome": True,
        "single_responsibility": True,
        "inputs_explicit": True,
        "outputs_explicit": True,
        "design_rules_and_interfaces_cited": True,
        "single_verification_boundary": True,
        "failure_isolated": True,
        "context_fits": True,
        "no_unapproved_decisions": True,
    }
    values.update(changes)
    return LeafReadinessClaim.model_validate(values, strict=True)


def work_node(
    node_id: str,
    node_type: str,
    owner_role: str,
    *,
    parent_id: str | None = "wn_outcome",
    depends_on: tuple[str, ...] = (),
    criteria: tuple[str, ...] = ("criterion_delivery",),
    produces: tuple[str, ...] = (),
    consumes: tuple[str, ...] = (),
    touch_points: tuple[str, ...] = (),
    output_consumers: tuple[str, ...] = (),
    non_blocking: tuple[str, ...] = (),
    leaf_claim: LeafReadinessClaim | None = None,
) -> WorkNodeProposal:
    interfaces = tuple(f"Design interface {name}" for name in produces + consumes)
    payload: dict[str, Any] = {
        "work_node_id": node_id,
        "parent_id": parent_id,
        "node_type": node_type,
        "goal": f"Deliver {node_id}",
        "owner_role": owner_role,
        "design_refs": (
            DesignReference(design_version=1, section="4.2", decision_ids=()),
        ),
        "depends_on": depends_on,
        "inputs": (),
        "expected_outputs": (f"output for {node_id}",),
        "output_consumer_ids": output_consumers,
        "interfaces": interfaces,
        "produces_interfaces": produces,
        "consumes_interfaces": consumes,
        "acceptance_criterion_ids": criteria,
        "expected_touch_points": touch_points,
        "non_blocking_dependencies": non_blocking,
        "leaf_readiness": leaf_claim,
    }
    return WorkNodeProposal.model_validate(payload, strict=True)


def valid_nodes() -> tuple[WorkNodeProposal, ...]:
    return (
        work_node(
            "wn_outcome",
            "OUTCOME",
            "coordinator",
            parent_id=None,
            leaf_claim=None,
        ),
        work_node(
            "wn_implementation",
            "LEAF_TASK",
            "executor",
            produces=("delivery-api",),
            output_consumers=("wn_integration",),
            touch_points=("src/delivery.py",),
            leaf_claim=ready_claim(),
        ),
        work_node(
            "wn_integration",
            "INTEGRATION",
            "integrator",
            depends_on=("wn_implementation",),
            consumes=("delivery-api",),
            produces=("integrated-delivery",),
            output_consumers=("wn_outcome_verification",),
            leaf_claim=ready_claim(),
        ),
        work_node(
            "wn_outcome_verification",
            "VERIFICATION",
            "outcome-verifier",
            depends_on=("wn_integration",),
            consumes=("integrated-delivery",),
            leaf_claim=ready_claim(),
        ),
    )


def proposed_plan(
    nodes: tuple[WorkNodeProposal, ...] | None = None,
) -> ProposedWorkPlan:
    return ProposedWorkPlan(
        context=SubmissionContext(
            proposal_id="proposal_plan_validator",
            run_id="run_plan_validator",
            work_node_id="wn_outcome",
            attempt_id="attempt_plan_validator",
            submitted_at=datetime(2026, 7, 16, 8, tzinfo=UTC),
            producer=AgentActor(
                actor_id="agent_work-planner",
                kind="agent",
                role="work-planner",
            ),
            design_version=1,
        ),
        root_work_node_id="wn_outcome",
        nodes=nodes or valid_nodes(),
    )


def validate(plan: ProposedWorkPlan) -> ApprovedWorkPlan | RejectedWorkPlan:
    return validate_work_plan(
        plan,
        charter_criterion_ids=("criterion_delivery",),
        protected_artifacts=("secrets", "policy/locked.md"),
    )


def rule_ids(result: RejectedWorkPlan) -> set[str]:
    return {rejection.rule_id for rejection in result.rejections}


def replace_node(
    nodes: tuple[WorkNodeProposal, ...],
    node_id: str,
    **changes: object,
) -> tuple[WorkNodeProposal, ...]:
    return tuple(
        node.model_copy(update=changes) if node.work_node_id == node_id else node
        for node in nodes
    )


def test_valid_dag_is_normalized_and_exposes_only_exact_ready_frontier() -> None:
    result = validate(proposed_plan())

    assert isinstance(result, ApprovedWorkPlan)
    assert [node.work_node_id for node in result.nodes] == [
        "wn_outcome",
        "wn_implementation",
        "wn_integration",
        "wn_outcome_verification",
    ]
    assert [node.topological_index for node in result.nodes] == [0, 1, 2, 3]
    assert result.nodes[0].child_ids == (
        "wn_implementation",
        "wn_integration",
        "wn_outcome_verification",
    )
    assert result.nodes[0].disposition == "DECOMPOSED"
    assert result.nodes[1].disposition == "READY"
    assert result.nodes[2].disposition == "WAITING"
    assert result.nodes[2].readiness_rejections[0].rule_id == (
        "leaf.dependency_not_ready"
    )
    assert result.ready_frontier == ("wn_implementation",)


def test_generated_input_orders_have_identical_canonical_graph_order() -> None:
    nodes = valid_nodes()
    observed = set()

    for ordering in permutations(nodes):
        result = validate(proposed_plan(ordering))
        assert isinstance(result, ApprovedWorkPlan)
        observed.add(tuple(node.work_node_id for node in result.nodes))

    assert observed == {
        (
            "wn_outcome",
            "wn_implementation",
            "wn_integration",
            "wn_outcome_verification",
        )
    }


def test_design_decision_references_are_canonicalized_recursively() -> None:
    forward_reference = DesignReference(
        design_version=1,
        section="4.2",
        decision_ids=("decision_alpha", "decision_beta"),
    )
    reverse_reference = DesignReference(
        design_version=1,
        section="4.2",
        decision_ids=("decision_beta", "decision_alpha"),
    )
    forward_nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        design_refs=(forward_reference,),
    )
    reverse_nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        design_refs=(reverse_reference,),
    )

    forward = validate(proposed_plan(forward_nodes))
    reverse = validate(proposed_plan(reverse_nodes))

    assert isinstance(forward, ApprovedWorkPlan)
    assert isinstance(reverse, ApprovedWorkPlan)
    assert forward == reverse
    implementation = next(
        node for node in forward.nodes if node.work_node_id == "wn_implementation"
    )
    assert implementation.design_refs[0].decision_ids == (
        "decision_alpha",
        "decision_beta",
    )


def test_duplicate_nested_design_decision_reference_is_rejected() -> None:
    duplicate_reference = DesignReference(
        design_version=1,
        section="4.2",
        decision_ids=("decision_alpha", "decision_alpha"),
    )
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        design_refs=(duplicate_reference,),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    duplicates = [
        rejection
        for rejection in result.rejections
        if rejection.rule_id == "plan.duplicate_reference"
    ]
    assert [(reason.work_node_id, reason.reference) for reason in duplicates] == [
        ("wn_implementation", "decision_alpha")
    ]


def test_design_reference_version_must_match_approved_baseline() -> None:
    mismatched_reference = DesignReference(
        design_version=999,
        section="4.2",
        decision_ids=(),
    )
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        design_refs=(mismatched_reference,),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    mismatches = [
        rejection
        for rejection in result.rejections
        if rejection.rule_id == "plan.design_version_mismatch"
    ]
    assert [
        (reason.message, reason.work_node_id, reason.reference) for reason in mismatches
    ] == [
        (
            "design reference version does not match approved baseline",
            "wn_implementation",
            "expected 1, got 999",
        )
    ]


@pytest.mark.parametrize("cycle_size", range(2, 9))
def test_generated_dependency_cycles_are_rejected(cycle_size: int) -> None:
    nodes = list(valid_nodes())
    generated = []
    for index in range(cycle_size):
        generated.append(
            work_node(
                f"wn_cycle_{index}",
                "WORK_PACKAGE",
                "work-planner",
                depends_on=(f"wn_cycle_{(index - 1) % cycle_size}",),
            )
        )
    nodes.extend(generated)

    result = validate(proposed_plan(tuple(nodes)))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.dependency_cycle",
        "plan.execution_cycle",
    }.issubset(rule_ids(result))


def test_duplicate_and_dangling_references_are_rejected_atomically() -> None:
    nodes = valid_nodes()
    duplicate = nodes[1]
    nodes = replace_node(
        nodes,
        "wn_integration",
        depends_on=("wn_missing", "wn_missing"),
        non_blocking_dependencies=("wn_unlisted",),
    )
    result = validate(proposed_plan((*nodes, duplicate)))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.duplicate_node_id",
        "plan.duplicate_reference",
        "plan.dangling_dependency",
    }.issubset(rule_ids(result))
    assert not isinstance(result, ApprovedWorkPlan)


def test_parent_cycle_and_orphan_have_stable_rule_ids() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_outcome",
        parent_id="wn_implementation",
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.root_parent_present",
        "plan.parent_cycle",
        "plan.execution_cycle",
    }.issubset(rule_ids(result))


def test_dangling_parent_and_unknown_constructed_type_are_rejected() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        parent_id="wn_missing_parent",
    )
    unknown_type = nodes[0].model_copy(update={"node_type": "INVENTED"})
    nodes = (unknown_type, *nodes[1:])

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.dangling_parent",
        "plan.unknown_node_type",
    }.issubset(rule_ids(result))


@pytest.mark.parametrize(
    ("node_id", "owner_role", "expected_rule"),
    (
        ("wn_implementation", "invented-worker", "plan.unknown_owner"),
        ("wn_implementation", "work-planner", "plan.authority_violation"),
        ("wn_integration", "executor", "plan.authority_violation"),
        ("wn_outcome_verification", "local-verifier", "plan.authority_violation"),
    ),
)
def test_known_owner_and_authority_policy_is_enforced(
    node_id: str, owner_role: str, expected_rule: str
) -> None:
    nodes = replace_node(valid_nodes(), node_id, owner_role=owner_role)

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert expected_rule in rule_ids(result)


def test_directly_executable_work_package_requires_execute_authority() -> None:
    planner_owned = replace_node(
        valid_nodes(),
        "wn_implementation",
        node_type="WORK_PACKAGE",
        owner_role="work-planner",
    )

    rejected = validate(proposed_plan(planner_owned))

    assert isinstance(rejected, RejectedWorkPlan)
    assert "plan.authority_violation" in rule_ids(rejected)

    executor_owned = replace_node(
        planner_owned,
        "wn_implementation",
        owner_role="executor",
    )

    approved = validate(proposed_plan(executor_owned))

    assert isinstance(approved, ApprovedWorkPlan)
    assert approved.ready_frontier == ("wn_implementation",)


def test_protected_artifact_ancestor_and_descendant_touches_are_rejected() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        expected_touch_points=("secrets/token.txt", "policy"),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    protected = [
        rejection
        for rejection in result.rejections
        if rejection.rule_id == "plan.protected_artifact"
    ]
    assert {rejection.reference for rejection in protected} == {
        "policy",
        "secrets/token.txt",
    }


def test_interface_requires_one_producer_and_dependency_order() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_integration",
        depends_on=(),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert "plan.interface_order_invalid" in rule_ids(result)


@pytest.mark.parametrize("transitive", (False, True))
def test_interface_order_cannot_use_non_blocking_dependencies(
    transitive: bool,
) -> None:
    nodes = valid_nodes()
    if transitive:
        adapter = work_node(
            "wn_adapter",
            "WORK_PACKAGE",
            "work-planner",
            depends_on=("wn_implementation",),
            output_consumers=("wn_integration",),
            non_blocking=("wn_implementation",),
        )
        nodes = replace_node(
            nodes,
            "wn_integration",
            depends_on=("wn_adapter",),
        )
        nodes = (*nodes, adapter)
    else:
        nodes = replace_node(
            nodes,
            "wn_integration",
            non_blocking_dependencies=("wn_implementation",),
        )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert "plan.interface_order_invalid" in rule_ids(result)


def test_outputs_require_consumers_and_integration_requires_delivery_order() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        output_consumer_ids=(),
        produces_interfaces=(),
        interfaces=(),
    )
    nodes = replace_node(
        nodes,
        "wn_integration",
        depends_on=(),
        consumes_interfaces=(),
        interfaces=("Design interface integrated-delivery",),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.output_consumer_missing",
        "plan.integration_order_invalid",
    }.issubset(rule_ids(result))


def test_final_verification_must_follow_integration() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_outcome_verification",
        depends_on=(),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert "plan.final_verification_order_invalid" in rule_ids(result)


def test_final_verification_must_follow_integration_for_each_criterion() -> None:
    root, implementation, delivery_integration, final_verification = valid_nodes()
    security_implementation = work_node(
        "wn_security_implementation",
        "LEAF_TASK",
        "executor",
        criteria=("criterion_security",),
        produces=("security-api",),
        output_consumers=("wn_security_integration",),
        leaf_claim=ready_claim(),
    )
    security_integration = work_node(
        "wn_security_integration",
        "INTEGRATION",
        "integrator",
        depends_on=("wn_security_implementation",),
        criteria=("criterion_security",),
        consumes=("security-api",),
        output_consumers=("wn_outcome_verification",),
        leaf_claim=ready_claim(),
    )
    final_verification = final_verification.model_copy(
        update={
            "acceptance_criterion_ids": (
                "criterion_delivery",
                "criterion_security",
            )
        }
    )
    nodes = (
        root,
        implementation,
        security_implementation,
        delivery_integration,
        security_integration,
        final_verification,
    )

    rejected = validate_work_plan(
        proposed_plan(nodes),
        charter_criterion_ids=("criterion_delivery", "criterion_security"),
    )

    assert isinstance(rejected, RejectedWorkPlan)
    invalid_order = [
        rejection
        for rejection in rejected.rejections
        if rejection.rule_id == "plan.final_verification_order_invalid"
    ]
    assert [(reason.work_node_id, reason.reference) for reason in invalid_order] == [
        ("wn_outcome_verification", "criterion_security")
    ]

    ordered_nodes = replace_node(
        nodes,
        "wn_outcome_verification",
        depends_on=("wn_integration", "wn_security_integration"),
    )

    approved = validate_work_plan(
        proposed_plan(ordered_nodes),
        charter_criterion_ids=("criterion_delivery", "criterion_security"),
    )

    assert isinstance(approved, ApprovedWorkPlan)


def test_missing_and_ambiguous_interface_ownership_are_rejected() -> None:
    nodes = replace_node(
        valid_nodes(),
        "wn_outcome",
        produces_interfaces=("delivery-api", "orphan-interface"),
    )

    result = validate(proposed_plan(nodes))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.interface_producer_ambiguous",
        "plan.interface_consumer_missing",
    }.issubset(rule_ids(result))


def test_every_charter_criterion_requires_delivery_integration_and_final_coverage() -> (
    None
):
    result = validate_work_plan(
        proposed_plan(),
        charter_criterion_ids=("criterion_delivery", "criterion_security"),
    )

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.criterion_uncovered",
        "plan.criterion_integration_uncovered",
        "plan.criterion_final_verification_uncovered",
    }.issubset(rule_ids(result))


def test_unknown_criterion_and_missing_assurance_nodes_are_rejected() -> None:
    root, implementation, _, _ = valid_nodes()
    implementation = implementation.model_copy(
        update={"acceptance_criterion_ids": ("criterion_invented",)}
    )

    result = validate(proposed_plan((root, implementation)))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "plan.criterion_unknown",
        "plan.integration_missing",
        "plan.final_verification_missing",
    }.issubset(rule_ids(result))


def test_non_leaf_executable_and_failed_leaf_claim_are_rejected() -> None:
    child = work_node(
        "wn_nested_leaf",
        "LEAF_TASK",
        "executor",
        parent_id="wn_implementation",
        leaf_claim=ready_claim(),
    )
    nodes = replace_node(
        valid_nodes(),
        "wn_implementation",
        leaf_readiness=ready_claim(context_fits=False),
    )

    result = validate(proposed_plan((*nodes, child)))

    assert isinstance(result, RejectedWorkPlan)
    assert {
        "leaf.executable_has_children",
        "leaf.context_fits",
    }.issubset(rule_ids(result))


def test_exact_leaf_readiness_reports_all_missing_or_untrusted_facts() -> None:
    node = work_node(
        "wn_leaf",
        "LEAF_TASK",
        "executor",
        depends_on=("wn_dependency",),
        criteria=(),
        leaf_claim=ready_claim(
            observable_outcome=False,
            inputs_explicit=False,
            no_unapproved_decisions=False,
        ),
    ).model_copy(update={"design_refs": (), "expected_outputs": ()})

    result = evaluate_leaf_readiness(
        node,
        child_ids=("wn_child",),
        verified_work_node_ids=(),
    )

    assert result.ready is False
    assert {rejection.rule_id for rejection in result.rejections} == {
        "leaf.executable_has_children",
        "leaf.observable_outcome",
        "leaf.inputs_explicit",
        "leaf.outputs_explicit",
        "leaf.design_rules_and_interfaces_cited",
        "leaf.single_verification_boundary",
        "leaf.unapproved_decision",
        "leaf.dependency_not_ready",
    }


def test_verified_or_explicitly_non_blocking_dependencies_make_leaf_ready() -> None:
    verified = work_node(
        "wn_verified_consumer",
        "LEAF_TASK",
        "executor",
        depends_on=("wn_verified",),
        leaf_claim=ready_claim(),
    )
    non_blocking = work_node(
        "wn_non_blocking_consumer",
        "LEAF_TASK",
        "executor",
        depends_on=("wn_pending",),
        non_blocking=("wn_pending",),
        leaf_claim=ready_claim(),
    )

    assert evaluate_leaf_readiness(
        verified, verified_work_node_ids=("wn_verified",)
    ).ready
    assert evaluate_leaf_readiness(non_blocking).ready


def test_count_and_depth_budgets_are_policy_controlled() -> None:
    plan = proposed_plan()

    count_result = validate_work_plan(
        plan,
        charter_criterion_ids=("criterion_delivery",),
        policy=PlanValidationPolicy(max_nodes=3, max_depth=8),
    )
    depth_result = validate_work_plan(
        plan,
        charter_criterion_ids=("criterion_delivery",),
        policy=PlanValidationPolicy(max_nodes=256, max_depth=0),
    )

    assert isinstance(count_result, RejectedWorkPlan)
    assert isinstance(depth_result, RejectedWorkPlan)
    assert "plan.node_count_exceeded" in rule_ids(count_result)
    assert "plan.depth_exceeded" in rule_ids(depth_result)
