"""The executable, fixed LangGraph topology for runner recovery."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

from orchestrator.domain import ControlStage
from orchestrator.runner.coordinator import (
    STAGE_STATUS_TARGETS,
    RunnerCoordinator,
    StageResult,
)
from orchestrator.runner.leases import RunLease


class StagePort(Protocol):
    """Application-stage boundary; production wiring is intentionally later."""

    def evaluate(self, *, run_id: str, stage: ControlStage) -> StageResult:
        """Return a schema-validated fixed-graph status, never executable routing."""


class ControlGraphState(TypedDict, total=False):
    route: ControlStage


class DeterministicNoModelStagePort:
    """A test-only deterministic stage fixture, not a model-runtime fallback."""

    def __init__(self, statuses: Mapping[ControlStage, StageResult]) -> None:
        self._statuses = dict(statuses)
        self.calls: list[ControlStage] = []

    def evaluate(self, *, run_id: str, stage: ControlStage) -> StageResult:
        del run_id
        self.calls.append(stage)
        try:
            return self._statuses[stage]
        except KeyError as error:
            raise RuntimeError(
                f"no deterministic result configured for {stage}"
            ) from error


def deterministic_happy_path() -> DeterministicNoModelStagePort:
    """Return the narrow no-model fixture used only by runner integration tests."""
    return DeterministicNoModelStagePort(
        {
            "INTAKE": StageResult("accepted"),
            "INVESTIGATE": StageResult("accepted"),
            "DESIGN": StageResult("accepted"),
            "DESIGN_CRITIQUE": StageResult("accepted"),
            "PLAN": StageResult("accepted"),
            "VALIDATE_PLAN": StageResult("accepted"),
            "DISPATCH": StageResult("accepted"),
            "EXECUTE": StageResult("accepted"),
            "LOCAL_VERIFY": StageResult("pass"),
            "INTEGRATE": StageResult("pass"),
            "OUTCOME_VERIFY": StageResult("pass"),
        }
    )


def compile_runner_graph(
    *,
    coordinator: RunnerCoordinator,
    stage_port: StagePort,
    lease_ref: list[RunLease],
    checkpointer: PostgresSaver,
) -> Any:
    """Compile exactly the code-owned graph and attach a PostgreSQL saver."""
    graph = StateGraph(ControlGraphState)

    for stage, statuses in STAGE_STATUS_TARGETS.items():
        if stage in {"COMPLETE", "BLOCKED"}:
            graph.add_node(stage, lambda state: state)
            graph.add_edge(stage, END)
            continue

        def node(
            state: ControlGraphState, *, fixed_stage: ControlStage = stage
        ) -> ControlGraphState:
            del state
            lease_ref[0] = coordinator._lease_queue.renew(lease_ref[0])
            result = stage_port.evaluate(run_id=lease_ref[0].run_id, stage=fixed_stage)
            lease_ref[0] = coordinator._lease_queue.renew(lease_ref[0])
            advanced = coordinator.advance(
                stage=fixed_stage, result=result, lease=lease_ref[0]
            )
            return {"route": advanced.target}

        graph.add_node(stage, node)
        graph.add_conditional_edges(
            stage,
            lambda state: state["route"],
            {target: target for target in statuses.values()},
        )

    graph.add_edge(START, "INTAKE")
    return graph.compile(checkpointer=checkpointer)
