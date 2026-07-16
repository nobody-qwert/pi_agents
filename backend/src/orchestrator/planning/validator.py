"""Side-effect-free policy validation for proposed work-plan DAGs."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final, Literal, get_args

from pydantic import Field

from orchestrator.domain.primitives import (
    ArtifactId,
    CriterionId,
    DesignReference,
    DesignVersion,
    NonEmptyStr,
    ProposalId,
    RelativePath,
    RunId,
    ShortStr,
    StrictDomainModel,
    WorkNodeId,
    WorkNodeType,
)
from orchestrator.domain.proposals import (
    LeafReadinessClaim,
    ProposedWorkPlan,
    WorkNodeProposal,
)

PlanRuleId = Literal[
    "plan.duplicate_node_id",
    "plan.root_missing",
    "plan.root_parent_present",
    "plan.orphan_node",
    "plan.dangling_parent",
    "plan.dangling_dependency",
    "plan.self_dependency",
    "plan.duplicate_reference",
    "plan.design_version_mismatch",
    "plan.parent_cycle",
    "plan.dependency_cycle",
    "plan.execution_cycle",
    "plan.node_count_exceeded",
    "plan.depth_exceeded",
    "plan.unknown_node_type",
    "plan.unknown_owner",
    "plan.authority_violation",
    "plan.protected_artifact",
    "plan.output_consumer_missing",
    "plan.output_consumer_invalid",
    "plan.interface_producer_missing",
    "plan.interface_producer_ambiguous",
    "plan.interface_consumer_missing",
    "plan.interface_order_invalid",
    "plan.criterion_unknown",
    "plan.criterion_uncovered",
    "plan.criterion_integration_uncovered",
    "plan.criterion_final_verification_uncovered",
    "plan.integration_missing",
    "plan.integration_order_invalid",
    "plan.final_verification_missing",
    "plan.final_verification_order_invalid",
    "leaf.executable_has_children",
    "leaf.claim_missing",
    "leaf.observable_outcome",
    "leaf.single_responsibility",
    "leaf.inputs_explicit",
    "leaf.outputs_explicit",
    "leaf.design_rules_and_interfaces_cited",
    "leaf.single_verification_boundary",
    "leaf.failure_isolated",
    "leaf.context_fits",
    "leaf.unapproved_decision",
    "leaf.dependency_not_ready",
]
OwnerCapability = Literal[
    "plan",
    "execute",
    "integrate",
    "verify_local",
    "verify_outcome",
    "decide",
]
NodeDisposition = Literal["DESIGNED", "DECOMPOSED", "WAITING", "READY"]


class PlanRejection(StrictDomainModel):
    """One stable, structured reason that a proposal or leaf is not acceptable."""

    rule_id: PlanRuleId
    message: NonEmptyStr
    work_node_id: WorkNodeId | None = None
    reference: NonEmptyStr | None = None


class LeafReadiness(StrictDomainModel):
    """Exact dispatch-readiness result for one proposed node."""

    work_node_id: WorkNodeId
    ready: bool
    rejections: tuple[PlanRejection, ...] = ()


class ApprovedWorkNode(StrictDomainModel):
    """Normalized work-node data produced by successful validation."""

    work_node_id: WorkNodeId
    parent_id: WorkNodeId | None
    node_type: WorkNodeType
    goal: NonEmptyStr
    owner_role: ShortStr
    design_refs: tuple[DesignReference, ...]
    depends_on: tuple[WorkNodeId, ...]
    inputs: tuple[ArtifactId, ...]
    expected_outputs: tuple[ShortStr, ...]
    output_consumer_ids: tuple[WorkNodeId, ...]
    interfaces: tuple[NonEmptyStr, ...]
    produces_interfaces: tuple[ShortStr, ...]
    consumes_interfaces: tuple[ShortStr, ...]
    acceptance_criterion_ids: tuple[CriterionId, ...]
    expected_touch_points: tuple[RelativePath, ...]
    non_blocking_dependencies: tuple[WorkNodeId, ...]
    leaf_readiness: LeafReadinessClaim | None
    child_ids: tuple[WorkNodeId, ...]
    depth: int = Field(ge=0)
    topological_index: int = Field(ge=0)
    disposition: NodeDisposition
    readiness_rejections: tuple[PlanRejection, ...] = ()


class ApprovedWorkPlan(StrictDomainModel):
    """Whole-plan acceptance with canonical ordering and a ready frontier."""

    kind: Literal["approved_work_plan"] = "approved_work_plan"
    proposal_id: ProposalId
    run_id: RunId
    design_version: DesignVersion
    root_work_node_id: WorkNodeId
    nodes: tuple[ApprovedWorkNode, ...]
    ready_frontier: tuple[WorkNodeId, ...]


class RejectedWorkPlan(StrictDomainModel):
    """Whole-plan rejection; no approved subgraph is exposed."""

    kind: Literal["rejected_work_plan"] = "rejected_work_plan"
    proposal_id: ProposalId
    rejections: tuple[PlanRejection, ...] = Field(min_length=1)


type WorkPlanValidationResult = ApprovedWorkPlan | RejectedWorkPlan


def _default_owner_capabilities() -> Mapping[str, frozenset[OwnerCapability]]:
    return MappingProxyType(
        {
            "coordinator": frozenset({"plan"}),
            "work-planner": frozenset({"plan"}),
            "executor": frozenset({"execute"}),
            "integrator": frozenset({"integrate"}),
            "local-verifier": frozenset({"verify_local"}),
            "outcome-verifier": frozenset({"verify_outcome"}),
            "design-authority": frozenset({"decide"}),
            "human-decision-owner": frozenset({"decide"}),
        }
    )


@dataclass(frozen=True, slots=True)
class PlanValidationPolicy:
    """Closed budgets and role capabilities used by deterministic validation."""

    max_nodes: int = 256
    max_depth: int = 8
    owner_capabilities: Mapping[str, frozenset[OwnerCapability]] = field(
        default_factory=_default_owner_capabilities
    )

    def __post_init__(self) -> None:
        if self.max_nodes < 1:
            raise ValueError("max_nodes must be at least one")
        if self.max_depth < 0:
            raise ValueError("max_depth cannot be negative")
        known_capabilities = set(get_args(OwnerCapability))
        normalized: dict[str, frozenset[OwnerCapability]] = {}
        for owner, capabilities in self.owner_capabilities.items():
            if not owner.strip():
                raise ValueError("owner role must not be empty")
            unknown = set(capabilities).difference(known_capabilities)
            if unknown:
                raise ValueError(
                    f"unknown capabilities for {owner}: {', '.join(sorted(unknown))}"
                )
            normalized[owner] = frozenset(capabilities)
        object.__setattr__(self, "owner_capabilities", MappingProxyType(normalized))


DEFAULT_PLAN_VALIDATION_POLICY: Final = PlanValidationPolicy()
_EXECUTABLE_TYPES: Final[frozenset[WorkNodeType]] = frozenset(
    {"LEAF_TASK", "INTEGRATION", "VERIFICATION"}
)
_CLAIM_RULES: Final[tuple[tuple[str, PlanRuleId, str], ...]] = (
    ("observable_outcome", "leaf.observable_outcome", "outcome is not observable"),
    (
        "single_responsibility",
        "leaf.single_responsibility",
        "ownership is not one bounded responsibility",
    ),
    ("inputs_explicit", "leaf.inputs_explicit", "required inputs are not explicit"),
    (
        "outputs_explicit",
        "leaf.outputs_explicit",
        "expected outputs are not explicit",
    ),
    (
        "design_rules_and_interfaces_cited",
        "leaf.design_rules_and_interfaces_cited",
        "design rules or interfaces are not cited",
    ),
    (
        "single_verification_boundary",
        "leaf.single_verification_boundary",
        "acceptance has no single independent verification boundary",
    ),
    (
        "failure_isolated",
        "leaf.failure_isolated",
        "failure cannot be isolated from unrelated work",
    ),
    ("context_fits", "leaf.context_fits", "required context exceeds one worker"),
    (
        "no_unapproved_decisions",
        "leaf.unapproved_decision",
        "work requires an unapproved decision",
    ),
)


class WorkPlanValidator:
    """Reusable validator with immutable policy and no external effects."""

    def __init__(self, policy: PlanValidationPolicy = DEFAULT_PLAN_VALIDATION_POLICY):
        self._policy = policy

    def validate(
        self,
        proposal: ProposedWorkPlan,
        *,
        charter_criterion_ids: Iterable[CriterionId],
        protected_artifacts: Iterable[RelativePath] = (),
        verified_work_node_ids: Iterable[WorkNodeId] = (),
    ) -> WorkPlanValidationResult:
        return validate_work_plan(
            proposal,
            charter_criterion_ids=charter_criterion_ids,
            protected_artifacts=protected_artifacts,
            verified_work_node_ids=verified_work_node_ids,
            policy=self._policy,
        )


def evaluate_leaf_readiness(
    node: WorkNodeProposal,
    *,
    child_ids: Iterable[WorkNodeId] = (),
    verified_work_node_ids: Iterable[WorkNodeId] = (),
) -> LeafReadiness:
    """Evaluate every leaf and dispatch criterion without guessing missing facts."""

    rejections: list[PlanRejection] = []
    children = tuple(child_ids)
    if children:
        rejections.append(
            _rejection(
                "leaf.executable_has_children",
                "an executable node must be a graph leaf",
                node,
            )
        )
    claim = node.leaf_readiness
    if claim is None:
        rejections.append(
            _rejection(
                "leaf.claim_missing",
                "executable node has no explicit leaf-readiness claim",
                node,
            )
        )
    else:
        for field_name, rule_id, message in _CLAIM_RULES:
            if not getattr(claim, field_name):
                rejections.append(_rejection(rule_id, message, node))

    if not node.expected_outputs:
        rejections.append(
            _rejection("leaf.outputs_explicit", "expected outputs are empty", node)
        )
    if not node.design_refs or (
        (node.produces_interfaces or node.consumes_interfaces) and not node.interfaces
    ):
        rejections.append(
            _rejection(
                "leaf.design_rules_and_interfaces_cited",
                "versioned design or interface citations are missing",
                node,
            )
        )
    if not node.acceptance_criterion_ids:
        rejections.append(
            _rejection(
                "leaf.single_verification_boundary",
                "no acceptance criterion is assigned",
                node,
            )
        )

    verified = frozenset(verified_work_node_ids)
    non_blocking = frozenset(node.non_blocking_dependencies)
    for dependency in sorted(set(node.depends_on).difference(verified, non_blocking)):
        rejections.append(
            _rejection(
                "leaf.dependency_not_ready",
                "dependency is neither verified nor declared non-blocking",
                node,
                dependency,
            )
        )
    normalized = _sort_rejections(rejections)
    return LeafReadiness(
        work_node_id=node.work_node_id,
        ready=not normalized,
        rejections=normalized,
    )


def validate_work_plan(
    proposal: ProposedWorkPlan,
    *,
    charter_criterion_ids: Iterable[CriterionId],
    protected_artifacts: Iterable[RelativePath] = (),
    verified_work_node_ids: Iterable[WorkNodeId] = (),
    policy: PlanValidationPolicy = DEFAULT_PLAN_VALIDATION_POLICY,
) -> WorkPlanValidationResult:
    """Validate a complete proposal atomically and normalize it when accepted."""

    nodes = proposal.nodes
    rejections: list[PlanRejection] = []
    counts = Counter(node.work_node_id for node in nodes)
    for node_id, count in sorted(counts.items()):
        if count > 1:
            rejections.append(
                PlanRejection(
                    rule_id="plan.duplicate_node_id",
                    message=f"work node ID occurs {count} times",
                    work_node_id=node_id,
                )
            )
    by_id = {node.work_node_id: node for node in reversed(nodes)}
    if proposal.root_work_node_id not in by_id:
        rejections.append(
            PlanRejection(
                rule_id="plan.root_missing",
                message="declared root does not exist in the proposal",
                reference=proposal.root_work_node_id,
            )
        )
    if len(nodes) > policy.max_nodes:
        rejections.append(
            PlanRejection(
                rule_id="plan.node_count_exceeded",
                message=f"node count {len(nodes)} exceeds budget {policy.max_nodes}",
            )
        )

    children: dict[WorkNodeId, list[WorkNodeId]] = defaultdict(list)
    dependency_successors: dict[WorkNodeId, set[WorkNodeId]] = defaultdict(set)
    dependency_indegree: dict[WorkNodeId, int] = dict.fromkeys(by_id, 0)
    blocking_dependency_successors: dict[WorkNodeId, set[WorkNodeId]] = defaultdict(set)
    execution_successors: dict[WorkNodeId, set[WorkNodeId]] = defaultdict(set)
    indegree: dict[WorkNodeId, int] = dict.fromkeys(by_id, 0)

    known_types = set(get_args(WorkNodeType))
    for node_id in sorted(by_id):
        node = by_id[node_id]
        if node.node_type not in known_types:
            rejections.append(
                _rejection(
                    "plan.unknown_node_type", "node type is not recognized", node
                )
            )
        _validate_unique_references(node, rejections)
        _validate_design_baseline(
            node,
            proposal.context.design_version,
            rejections,
        )
        if node.work_node_id == proposal.root_work_node_id:
            if node.parent_id is not None:
                rejections.append(
                    _rejection(
                        "plan.root_parent_present",
                        "the declared root must not have a parent",
                        node,
                        node.parent_id,
                    )
                )
        elif node.parent_id is None:
            rejections.append(
                _rejection(
                    "plan.orphan_node",
                    "every non-root node must declare a parent",
                    node,
                )
            )
        if node.parent_id is not None:
            if node.parent_id not in by_id:
                rejections.append(
                    _rejection(
                        "plan.dangling_parent",
                        "parent reference does not exist",
                        node,
                        node.parent_id,
                    )
                )
            else:
                children[node.parent_id].append(node.work_node_id)
                _add_edge(
                    node.parent_id, node.work_node_id, execution_successors, indegree
                )

        for dependency in node.depends_on:
            if dependency == node.work_node_id:
                rejections.append(
                    _rejection(
                        "plan.self_dependency",
                        "node cannot depend on itself",
                        node,
                        dependency,
                    )
                )
            elif dependency not in by_id:
                rejections.append(
                    _rejection(
                        "plan.dangling_dependency",
                        "dependency reference does not exist",
                        node,
                        dependency,
                    )
                )
            else:
                _add_edge(
                    dependency,
                    node.work_node_id,
                    dependency_successors,
                    dependency_indegree,
                )
                if dependency not in node.non_blocking_dependencies:
                    blocking_dependency_successors[dependency].add(node.work_node_id)
                _add_edge(dependency, node.work_node_id, execution_successors, indegree)
        unknown_non_blocking = set(node.non_blocking_dependencies).difference(
            node.depends_on
        )
        for dependency in sorted(unknown_non_blocking):
            rejections.append(
                _rejection(
                    "plan.dangling_dependency",
                    "non-blocking reference is not a declared dependency",
                    node,
                    dependency,
                )
            )

    parent_cycle = _find_parent_cycle(by_id)
    if parent_cycle:
        rejections.append(
            PlanRejection(
                rule_id="plan.parent_cycle",
                message="parent relation contains a cycle",
                work_node_id=parent_cycle[0],
                reference=_bounded_reference(" -> ".join(parent_cycle)),
            )
        )
    dependency_order = _topological_order(
        dependency_successors,
        dependency_indegree,
    )
    if len(dependency_order) != len(by_id):
        cyclic_ids = sorted(set(by_id).difference(dependency_order))
        rejections.append(
            PlanRejection(
                rule_id="plan.dependency_cycle",
                message="dependency edges do not form an acyclic graph",
                work_node_id=cyclic_ids[0] if cyclic_ids else None,
                reference=_bounded_reference(
                    ",".join(cyclic_ids) if cyclic_ids else None
                ),
            )
        )
    order = _topological_order(execution_successors, indegree)
    if len(order) != len(by_id):
        cyclic_ids = sorted(set(by_id).difference(order))
        rejections.append(
            PlanRejection(
                rule_id="plan.execution_cycle",
                message="parent and dependency edges do not form an acyclic graph",
                work_node_id=cyclic_ids[0] if cyclic_ids else None,
                reference=_bounded_reference(
                    ",".join(cyclic_ids) if cyclic_ids else None
                ),
            )
        )

    depths = _depths(proposal.root_work_node_id, children, by_id)
    for node_id, depth in sorted(depths.items()):
        if depth > policy.max_depth:
            rejections.append(
                _rejection(
                    "plan.depth_exceeded",
                    f"node depth {depth} exceeds budget {policy.max_depth}",
                    by_id[node_id],
                )
            )

    terminal_verifiers = _terminal_verifiers(by_id, dependency_successors)
    _validate_owners(by_id, terminal_verifiers, policy, rejections)
    _validate_protected_artifacts(by_id, protected_artifacts, rejections)
    _validate_outputs(
        by_id,
        terminal_verifiers,
        blocking_dependency_successors,
        rejections,
    )
    _validate_interfaces(by_id, blocking_dependency_successors, rejections)
    _validate_criterion_coverage(
        by_id,
        terminal_verifiers,
        blocking_dependency_successors,
        charter_criterion_ids,
        rejections,
    )

    readiness: dict[WorkNodeId, LeafReadiness] = {}
    verified = tuple(verified_work_node_ids)
    for node_id in sorted(by_id):
        node = by_id[node_id]
        if _is_executable(node):
            result = evaluate_leaf_readiness(
                node,
                child_ids=children[node_id],
                verified_work_node_ids=verified,
            )
            readiness[node_id] = result
            rejections.extend(
                rejection
                for rejection in result.rejections
                if rejection.rule_id != "leaf.dependency_not_ready"
            )

    if rejections:
        return RejectedWorkPlan(
            proposal_id=proposal.context.proposal_id,
            rejections=_sort_rejections(rejections),
        )

    index = {node_id: position for position, node_id in enumerate(order)}
    normalized_nodes: list[ApprovedWorkNode] = []
    for node_id in order:
        node = by_id[node_id]
        node_readiness = readiness.get(node_id)
        child_ids = tuple(sorted(children[node_id]))
        if child_ids:
            disposition: NodeDisposition = "DECOMPOSED"
        elif node_readiness is None:
            disposition = "DESIGNED"
        elif node_readiness.ready:
            disposition = "READY"
        else:
            disposition = "WAITING"
        normalized_nodes.append(
            ApprovedWorkNode(
                work_node_id=node.work_node_id,
                parent_id=node.parent_id,
                node_type=node.node_type,
                goal=node.goal,
                owner_role=node.owner_role,
                design_refs=tuple(
                    sorted(
                        (_normalize_design_reference(ref) for ref in node.design_refs),
                        key=lambda ref: (
                            ref.design_version,
                            ref.section,
                            ref.decision_ids,
                        ),
                    )
                ),
                depends_on=tuple(sorted(node.depends_on)),
                inputs=tuple(sorted(node.inputs)),
                expected_outputs=tuple(sorted(node.expected_outputs)),
                output_consumer_ids=tuple(sorted(node.output_consumer_ids)),
                interfaces=tuple(sorted(node.interfaces)),
                produces_interfaces=tuple(sorted(node.produces_interfaces)),
                consumes_interfaces=tuple(sorted(node.consumes_interfaces)),
                acceptance_criterion_ids=tuple(sorted(node.acceptance_criterion_ids)),
                expected_touch_points=tuple(sorted(node.expected_touch_points)),
                non_blocking_dependencies=tuple(sorted(node.non_blocking_dependencies)),
                leaf_readiness=node.leaf_readiness,
                child_ids=child_ids,
                depth=depths[node_id],
                topological_index=index[node_id],
                disposition=disposition,
                readiness_rejections=(
                    node_readiness.rejections if node_readiness is not None else ()
                ),
            )
        )
    frontier = tuple(
        node.work_node_id for node in normalized_nodes if node.disposition == "READY"
    )
    return ApprovedWorkPlan(
        proposal_id=proposal.context.proposal_id,
        run_id=proposal.context.run_id,
        design_version=proposal.context.design_version,
        root_work_node_id=proposal.root_work_node_id,
        nodes=tuple(normalized_nodes),
        ready_frontier=frontier,
    )


def _validate_unique_references(
    node: WorkNodeProposal, rejections: list[PlanRejection]
) -> None:
    reference_groups: tuple[tuple[str, Iterable[object]], ...] = (
        ("dependency", node.depends_on),
        ("non-blocking dependency", node.non_blocking_dependencies),
        ("input", node.inputs),
        ("expected output", node.expected_outputs),
        ("output consumer", node.output_consumer_ids),
        ("interface citation", node.interfaces),
        ("produced interface", node.produces_interfaces),
        ("consumed interface", node.consumes_interfaces),
        ("acceptance criterion", node.acceptance_criterion_ids),
        ("touch point", node.expected_touch_points),
        (
            "design reference",
            (
                (ref.design_version, ref.section, tuple(sorted(ref.decision_ids)))
                for ref in node.design_refs
            ),
        ),
    )
    for label, values in reference_groups:
        materialized = tuple(values)
        duplicates = sorted(
            (value for value, count in Counter(materialized).items() if count > 1),
            key=str,
        )
        for duplicate in duplicates:
            rejections.append(
                _rejection(
                    "plan.duplicate_reference",
                    f"{label} reference occurs more than once",
                    node,
                    str(duplicate),
                )
            )

    for design_ref in node.design_refs:
        duplicate_decisions = sorted(
            decision_id
            for decision_id, count in Counter(design_ref.decision_ids).items()
            if count > 1
        )
        for decision_id in duplicate_decisions:
            rejections.append(
                _rejection(
                    "plan.duplicate_reference",
                    "design decision reference occurs more than once",
                    node,
                    decision_id,
                )
            )


def _normalize_design_reference(reference: DesignReference) -> DesignReference:
    return reference.model_copy(
        update={"decision_ids": tuple(sorted(reference.decision_ids))}
    )


def _validate_design_baseline(
    node: WorkNodeProposal,
    design_version: DesignVersion,
    rejections: list[PlanRejection],
) -> None:
    for referenced_version in sorted(
        {
            reference.design_version
            for reference in node.design_refs
            if reference.design_version != design_version
        }
    ):
        rejections.append(
            _rejection(
                "plan.design_version_mismatch",
                "design reference version does not match approved baseline",
                node,
                f"expected {design_version}, got {referenced_version}",
            )
        )


def _validate_owners(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    terminal_verifiers: frozenset[WorkNodeId],
    policy: PlanValidationPolicy,
    rejections: list[PlanRejection],
) -> None:
    for node_id in sorted(by_id):
        node = by_id[node_id]
        capabilities = policy.owner_capabilities.get(node.owner_role)
        if capabilities is None:
            rejections.append(
                _rejection(
                    "plan.unknown_owner",
                    "owner role is not registered in plan policy",
                    node,
                    node.owner_role,
                )
            )
            continue
        required = _required_capability(node, node_id in terminal_verifiers)
        if required not in capabilities:
            rejections.append(
                _rejection(
                    "plan.authority_violation",
                    f"owner lacks required {required} authority",
                    node,
                    node.owner_role,
                )
            )


def _required_capability(
    node: WorkNodeProposal, terminal_verifier: bool
) -> OwnerCapability:
    if node.node_type == "LEAF_TASK":
        return "execute"
    if node.node_type == "INTEGRATION":
        return "integrate"
    if node.node_type == "VERIFICATION":
        return "verify_outcome" if terminal_verifier else "verify_local"
    if node.node_type == "DECISION":
        return "decide"
    if node.leaf_readiness is not None:
        return "execute"
    return "plan"


def _validate_protected_artifacts(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    protected_artifacts: Iterable[RelativePath],
    rejections: list[PlanRejection],
) -> None:
    protected = tuple(sorted(set(protected_artifacts)))
    for node_id in sorted(by_id):
        node = by_id[node_id]
        for touch_point in sorted(set(node.expected_touch_points)):
            for protected_path in protected:
                if _paths_overlap(touch_point, protected_path):
                    rejections.append(
                        _rejection(
                            "plan.protected_artifact",
                            f"touch point overlaps protected artifact {protected_path}",
                            node,
                            touch_point,
                        )
                    )


def _validate_outputs(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    terminal_verifiers: frozenset[WorkNodeId],
    blocking_dependency_successors: Mapping[WorkNodeId, set[WorkNodeId]],
    rejections: list[PlanRejection],
) -> None:
    for node_id in sorted(by_id):
        node = by_id[node_id]
        if (
            node.expected_outputs
            and node.node_type != "OUTCOME"
            and node_id not in terminal_verifiers
            and not node.output_consumer_ids
        ):
            rejections.append(
                _rejection(
                    "plan.output_consumer_missing",
                    "expected outputs have no declared consumer",
                    node,
                )
            )

        reachable = _reachable_from(node_id, blocking_dependency_successors)
        for consumer_id in sorted(set(node.output_consumer_ids)):
            if consumer_id not in by_id:
                rejections.append(
                    _rejection(
                        "plan.output_consumer_invalid",
                        "output consumer does not exist in the proposal",
                        node,
                        consumer_id,
                    )
                )
            elif consumer_id not in reachable:
                rejections.append(
                    _rejection(
                        "plan.output_consumer_invalid",
                        "output consumer does not have a blocking dependency path "
                        "from the producer",
                        node,
                        consumer_id,
                    )
                )


def _paths_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return left_path.is_relative_to(right_path) or right_path.is_relative_to(left_path)


def _validate_interfaces(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    blocking_dependency_successors: Mapping[WorkNodeId, set[WorkNodeId]],
    rejections: list[PlanRejection],
) -> None:
    producers: dict[str, list[WorkNodeId]] = defaultdict(list)
    consumers: dict[str, list[WorkNodeId]] = defaultdict(list)
    for node_id, node in by_id.items():
        for interface in node.produces_interfaces:
            producers[interface].append(node_id)
        for interface in node.consumes_interfaces:
            consumers[interface].append(node_id)

    for interface in sorted(set(producers).union(consumers)):
        interface_producers = sorted(producers[interface])
        interface_consumers = sorted(consumers[interface])
        if not interface_producers:
            for consumer in interface_consumers:
                rejections.append(
                    _rejection(
                        "plan.interface_producer_missing",
                        "consumed interface has no producer",
                        by_id[consumer],
                        interface,
                    )
                )
            continue
        if len(interface_producers) > 1:
            for producer in interface_producers:
                rejections.append(
                    _rejection(
                        "plan.interface_producer_ambiguous",
                        "interface has more than one producer",
                        by_id[producer],
                        interface,
                    )
                )
            continue
        producer = interface_producers[0]
        if not interface_consumers:
            rejections.append(
                _rejection(
                    "plan.interface_consumer_missing",
                    "produced interface has no consumer",
                    by_id[producer],
                    interface,
                )
            )
            continue
        reachable = _reachable_from(producer, blocking_dependency_successors)
        for consumer in interface_consumers:
            if consumer not in reachable:
                rejections.append(
                    _rejection(
                        "plan.interface_order_invalid",
                        "consumer has no blocking dependency path from its interface "
                        "producer",
                        by_id[consumer],
                        interface,
                    )
                )


def _validate_criterion_coverage(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    terminal_verifiers: frozenset[WorkNodeId],
    blocking_dependency_successors: Mapping[WorkNodeId, set[WorkNodeId]],
    charter_criterion_ids: Iterable[CriterionId],
    rejections: list[PlanRejection],
) -> None:
    criteria = frozenset(charter_criterion_ids)
    covered = frozenset(
        criterion
        for node in by_id.values()
        for criterion in node.acceptance_criterion_ids
    )
    for unknown in sorted(covered.difference(criteria)):
        rejections.append(
            PlanRejection(
                rule_id="plan.criterion_unknown",
                message="node references a criterion outside the charter",
                reference=unknown,
            )
        )
    for criterion in sorted(criteria.difference(covered)):
        rejections.append(
            PlanRejection(
                rule_id="plan.criterion_uncovered",
                message="charter criterion is not mapped to any node",
                reference=criterion,
            )
        )

    integrations = [node for node in by_id.values() if node.node_type == "INTEGRATION"]
    if not integrations:
        rejections.append(
            PlanRejection(
                rule_id="plan.integration_missing",
                message="plan has no integration node",
            )
        )
    integration_coverage = {
        criterion
        for node in integrations
        for criterion in node.acceptance_criterion_ids
    }
    for criterion in sorted(criteria.difference(integration_coverage)):
        rejections.append(
            PlanRejection(
                rule_id="plan.criterion_integration_uncovered",
                message="charter criterion is not covered by integration",
                reference=criterion,
            )
        )
    delivery_ids = {
        node_id for node_id, node in by_id.items() if _is_delivery_node(node)
    }
    for integration in sorted(integrations, key=lambda node: node.work_node_id):
        preceding_delivery_ids = {
            delivery_id
            for delivery_id in delivery_ids
            if integration.work_node_id
            in _reachable_from(delivery_id, blocking_dependency_successors)
        }
        for criterion in sorted(integration.acceptance_criterion_ids):
            if not any(
                criterion in by_id[delivery_id].acceptance_criterion_ids
                for delivery_id in preceding_delivery_ids
            ):
                rejections.append(
                    _rejection(
                        "plan.integration_order_invalid",
                        "integration does not follow delivery work covering the same "
                        "criterion",
                        integration,
                        criterion,
                    )
                )

    if not terminal_verifiers:
        rejections.append(
            PlanRejection(
                rule_id="plan.final_verification_missing",
                message="plan has no terminal outcome-verification node",
            )
        )
    integration_ids = {node.work_node_id for node in integrations}
    for verifier_id in sorted(terminal_verifiers):
        preceding_integrations = {
            integration_id
            for integration_id in integration_ids
            if verifier_id
            in _reachable_from(
                integration_id,
                blocking_dependency_successors,
            )
        }
        if not preceding_integrations:
            rejections.append(
                _rejection(
                    "plan.final_verification_order_invalid",
                    "final verification does not depend on an integration node",
                    by_id[verifier_id],
                )
            )
            continue
        for criterion in sorted(by_id[verifier_id].acceptance_criterion_ids):
            if not any(
                criterion in by_id[integration_id].acceptance_criterion_ids
                for integration_id in preceding_integrations
            ):
                rejections.append(
                    _rejection(
                        "plan.final_verification_order_invalid",
                        "final verification does not follow an integration "
                        "covering the same criterion",
                        by_id[verifier_id],
                        criterion,
                    )
                )
    final_coverage = {
        criterion
        for node_id in terminal_verifiers
        for criterion in by_id[node_id].acceptance_criterion_ids
    }
    for criterion in sorted(criteria.difference(final_coverage)):
        rejections.append(
            PlanRejection(
                rule_id="plan.criterion_final_verification_uncovered",
                message="charter criterion lacks final verification coverage",
                reference=criterion,
            )
        )


def _terminal_verifiers(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
    dependency_successors: Mapping[WorkNodeId, set[WorkNodeId]],
) -> frozenset[WorkNodeId]:
    return frozenset(
        node_id
        for node_id, node in by_id.items()
        if node.node_type == "VERIFICATION" and not dependency_successors.get(node_id)
    )


def _is_executable(node: WorkNodeProposal) -> bool:
    return node.node_type in _EXECUTABLE_TYPES or node.leaf_readiness is not None


def _is_delivery_node(node: WorkNodeProposal) -> bool:
    return node.node_type == "LEAF_TASK" or (
        node.node_type in {"SYSTEM", "WORK_PACKAGE"} and node.leaf_readiness is not None
    )


def _find_parent_cycle(
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
) -> tuple[WorkNodeId, ...]:
    for start in sorted(by_id):
        positions: dict[WorkNodeId, int] = {}
        chain: list[WorkNodeId] = []
        current: WorkNodeId | None = start
        while current is not None and current in by_id:
            if current in positions:
                cycle = [*chain[positions[current] :], current]
                return tuple(cycle)
            positions[current] = len(chain)
            chain.append(current)
            current = by_id[current].parent_id
    return ()


def _depths(
    root: WorkNodeId,
    children: Mapping[WorkNodeId, list[WorkNodeId]],
    by_id: Mapping[WorkNodeId, WorkNodeProposal],
) -> dict[WorkNodeId, int]:
    if root not in by_id:
        return dict.fromkeys(by_id, 0)
    depths: dict[WorkNodeId, int] = {root: 0}
    stack = [root]
    while stack:
        parent = stack.pop()
        for child in children[parent]:
            if child not in depths:
                depths[child] = depths[parent] + 1
                stack.append(child)
    for node_id in by_id:
        depths.setdefault(node_id, 0)
    return depths


def _add_edge(
    source: WorkNodeId,
    target: WorkNodeId,
    successors: dict[WorkNodeId, set[WorkNodeId]],
    indegree: dict[WorkNodeId, int],
) -> None:
    if target not in successors[source]:
        successors[source].add(target)
        indegree[target] += 1


def _topological_order(
    successors: Mapping[WorkNodeId, set[WorkNodeId]],
    indegree: Mapping[WorkNodeId, int],
) -> tuple[WorkNodeId, ...]:
    remaining = dict(indegree)
    ready = [node_id for node_id, count in remaining.items() if count == 0]
    heapify(ready)
    order: list[WorkNodeId] = []
    while ready:
        node_id = heappop(ready)
        order.append(node_id)
        for successor in sorted(successors.get(node_id, ())):
            remaining[successor] -= 1
            if remaining[successor] == 0:
                heappush(ready, successor)
    return tuple(order)


def _reachable_from(
    start: WorkNodeId,
    successors: Mapping[WorkNodeId, set[WorkNodeId]],
) -> frozenset[WorkNodeId]:
    reached: set[WorkNodeId] = set()
    pending = list(successors.get(start, ()))
    while pending:
        node_id = pending.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        pending.extend(successors.get(node_id, ()))
    return frozenset(reached)


def _rejection(
    rule_id: PlanRuleId,
    message: str,
    node: WorkNodeProposal,
    reference: str | None = None,
) -> PlanRejection:
    return PlanRejection(
        rule_id=rule_id,
        message=message,
        work_node_id=node.work_node_id,
        reference=_bounded_reference(reference),
    )


def _sort_rejections(rejections: Iterable[PlanRejection]) -> tuple[PlanRejection, ...]:
    unique: dict[tuple[PlanRuleId, WorkNodeId | None, str | None], PlanRejection] = {}
    for item in rejections:
        key = (item.rule_id, item.work_node_id, item.reference)
        previous = unique.get(key)
        if previous is None or item.message < previous.message:
            unique[key] = item
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: tuple("" if part is None else str(part) for part in item),
        )
    )


def _bounded_reference(reference: str | None) -> str | None:
    if reference is None or len(reference) <= 4096:
        return reference
    return f"{reference[:4080]}...[truncated]"
