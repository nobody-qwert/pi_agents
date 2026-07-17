"""Deterministic ready-frontier selection and immutable packet claiming."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from orchestrator.domain.authoritative import PacketRecord, WorkNodeRecord
from orchestrator.domain.primitives import DesignVersion


class DispatchRejected(Exception):
    """A deterministic packet or claim policy rejection."""


@dataclass(frozen=True, slots=True)
class PacketClaim:
    packet_id: str
    work_node_id: str
    attempt_number: int


def ready_frontier(nodes: tuple[WorkNodeRecord, ...]) -> tuple[WorkNodeRecord, ...]:
    """Return only dependency-verified leaf nodes, in a stable order."""
    by_id = {node.work_node_id: node for node in nodes}
    ready = [
        node
        for node in nodes
        if node.node_type == "LEAF_TASK"
        and node.status == "READY"
        and all(
            dependency_id in by_id and by_id[dependency_id].status == "VERIFIED"
            for dependency_id in node.depends_on
        )
    ]
    return tuple(sorted(ready, key=lambda node: node.work_node_id))


class PacketDispatchService:
    """Keeps packet identity and attempts deterministic without invoking workers."""

    def __init__(self) -> None:
        self._packets: dict[str, PacketRecord] = {}
        self._attempts: dict[str, int] = {}

    def issue(
        self,
        packet: PacketRecord,
        *,
        node: WorkNodeRecord,
        dependency_states: Mapping[str, str],
        current_design_version: DesignVersion,
    ) -> PacketRecord:
        if packet.run_id != node.run_id or packet.work_node_id != node.work_node_id:
            raise DispatchRejected("packet_node_mismatch")
        if node.node_type != "LEAF_TASK" or node.status != "READY":
            raise DispatchRejected("node_not_ready_leaf")
        if any(
            dependency_states.get(node_id) != "VERIFIED" for node_id in node.depends_on
        ):
            raise DispatchRejected("dependency_not_verified")
        if any(
            ref.design_version != current_design_version
            for ref in packet.design_baseline
        ):
            raise DispatchRejected("stale_design_baseline")
        existing = self._packets.get(packet.packet_id)
        if existing is not None:
            if existing != packet:
                raise DispatchRejected("packet_id_conflict")
            return existing
        self._packets[packet.packet_id] = packet
        return packet

    def claim(self, packet_id: str, *, max_attempts: int) -> PacketClaim:
        if max_attempts < 1:
            raise DispatchRejected("invalid_attempt_budget")
        packet = self._packets.get(packet_id)
        if packet is None:
            raise DispatchRejected("packet_not_issued")
        attempt_number = self._attempts.get(packet_id, 0) + 1
        if attempt_number > max_attempts:
            raise DispatchRejected("attempt_budget_exhausted")
        self._attempts[packet_id] = attempt_number
        return PacketClaim(
            packet_id=packet.packet_id,
            work_node_id=packet.work_node_id,
            attempt_number=attempt_number,
        )
