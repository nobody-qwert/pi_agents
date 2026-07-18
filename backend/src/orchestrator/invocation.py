"""The untrusted agent invocation boundary.

This service resolves a pinned registry definition, assembles only explicitly
referenced artifact input, and parses exactly the configured result schema.  It
does not accept the result into authoritative state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError

from orchestrator.artifacts import ArtifactService
from orchestrator.artifacts.models import ArtifactAccessRequest, ArtifactReference
from orchestrator.domain import (
    ApprovalProposal,
    CharterProposal,
    DesignCritiqueReport,
    DesignProposal,
    IntegrationReport,
    InvestigationReport,
    IssueReport,
    OutcomeEvidence,
    ProposedWorkPlan,
    StrictDomainModel,
    VerificationReport,
    WorkReport,
)
from orchestrator.domain.primitives import AttemptId, DesignVersion, RunId, WorkNodeId
from orchestrator.graph.registry import SCHEMA_REGISTRY, AgentRegistry
from orchestrator.model_gateway import GatewayFailure, ModelGateway, ModelRequest

_UNTRUSTED_RESULT_TYPES: Final[tuple[type[StrictDomainModel], ...]] = (
    ApprovalProposal,
    CharterProposal,
    DesignCritiqueReport,
    DesignProposal,
    IntegrationReport,
    InvestigationReport,
    IssueReport,
    OutcomeEvidence,
    ProposedWorkPlan,
    VerificationReport,
    WorkReport,
)


class InvocationRejected(Exception):
    """A bounded diagnostic that deliberately never contains prompt or model text."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InvocationInput:
    agent_id: str
    run_id: RunId
    attempt_id: AttemptId
    design_version: DesignVersion
    work_node_id: WorkNodeId | None
    tenant_id: str
    input_artifacts: tuple[ArtifactReference, ...] = ()
    context_payload: Mapping[str, object] | None = None
    max_context_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class InvocationResult:
    result: StrictDomainModel
    agent_id: str
    registry_hash: str
    config_hash: str
    prompt_hash: str
    model_id: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


class AgentInvocationService:
    """Invoke an agent once; callers, not malformed output, own retry policy."""

    def __init__(
        self,
        registry: AgentRegistry,
        gateway: ModelGateway,
        artifacts: ArtifactService | None,
    ) -> None:
        self._registry = registry
        self._gateway = gateway
        self._artifacts = artifacts

    def invoke(self, invocation: InvocationInput) -> InvocationResult:
        if invocation.max_context_bytes < 1:
            raise InvocationRejected("invalid_context_limit")
        try:
            definition = self._registry[invocation.agent_id]
        except KeyError as error:
            raise InvocationRejected("unknown_agent") from error
        output_type = SCHEMA_REGISTRY.get(definition.config.output_schema)
        if output_type is None or output_type not in _UNTRUSTED_RESULT_TYPES:
            raise InvocationRejected("unsafe_output_schema")
        context = self._assemble_context(invocation, output_type)
        try:
            response = self._gateway.complete(
                ModelRequest(
                    agent_id=definition.config.agent_id,
                    system_prompt=definition.prompt,
                    user_prompt=context,
                    max_output_tokens=definition.config.model.max_output_tokens,
                    temperature=definition.config.model.temperature,
                )
            )
        except GatewayFailure as error:
            raise InvocationRejected(
                f"gateway_{error.code}", retryable=error.retryable
            ) from error
        try:
            parsed = output_type.model_validate_json(response.content)
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            raise InvocationRejected("invalid_structured_output") from error
        self._validate_provenance(parsed, invocation, definition.config.agent_id)
        self._validate_authority(
            parsed, definition.config.authority.granted_capabilities()
        )
        return InvocationResult(
            result=parsed,
            agent_id=definition.config.agent_id,
            registry_hash=self._registry.registry_hash,
            config_hash=definition.config_hash,
            prompt_hash=definition.prompt_hash,
            model_id=response.model_id,
            finish_reason=response.finish_reason,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )

    def _assemble_context(
        self,
        invocation: InvocationInput,
        output_type: type[StrictDomainModel],
    ) -> str:
        sections: list[dict[str, object]] = []
        total_bytes = 0
        for reference in invocation.input_artifacts:
            if self._artifacts is None:
                raise InvocationRejected("artifact_service_unavailable")
            result = self._artifacts.read(
                reference,
                ArtifactAccessRequest(
                    tenant_id=invocation.tenant_id,
                    run_id=invocation.run_id,
                    role=invocation.agent_id,
                ),
            )
            total_bytes += len(result.content)
            if total_bytes > invocation.max_context_bytes:
                raise InvocationRejected("context_too_large")
            try:
                text = result.content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise InvocationRejected("non_text_context") from error
            sections.append(
                {
                    "artifact_id": reference.artifact_id,
                    "version": reference.version,
                    "media_type": result.metadata.media_type,
                    "content": text,
                }
            )
        envelope = {
            "run_id": invocation.run_id,
            "attempt_id": invocation.attempt_id,
            "design_version": invocation.design_version,
            "work_node_id": invocation.work_node_id,
            "artifacts": sections,
            "context": dict(invocation.context_payload or {}),
            "output_schema": output_type.model_json_schema(),
            "response_instruction": "Return only one JSON object matching your configured output schema.",
        }
        try:
            encoded = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise InvocationRejected("invalid_context_payload") from error
        if len(encoded.encode("utf-8")) > invocation.max_context_bytes:
            raise InvocationRejected("context_too_large")
        return encoded

    @staticmethod
    def _validate_provenance(
        result: StrictDomainModel,
        invocation: InvocationInput,
        agent_id: str,
    ) -> None:
        context = getattr(result, "context", None)
        if context is None:
            raise InvocationRejected("missing_submission_context")
        submission = getattr(context, "submission", context)
        expected_actor = f"agent_{agent_id.replace('-', '_')}"
        if (
            submission.run_id != invocation.run_id
            or submission.attempt_id != invocation.attempt_id
            or submission.design_version != invocation.design_version
            or submission.work_node_id != invocation.work_node_id
            or submission.producer.actor_id != expected_actor
            or submission.producer.role != agent_id
        ):
            raise InvocationRejected("stale_or_mismatched_provenance")

    @staticmethod
    def _validate_authority(
        result: StrictDomainModel, capabilities: frozenset[str]
    ) -> None:
        requirements: tuple[tuple[type[StrictDomainModel], str], ...] = (
            (CharterProposal, "can_propose_charter"),
            (DesignProposal, "can_propose_design"),
            (DesignCritiqueReport, "can_recommend_design_acceptance"),
            (InvestigationReport, "can_investigate_current_state"),
            (IntegrationReport, "can_integrate"),
            (ProposedWorkPlan, "can_propose_work_plan"),
            (IssueReport, "can_triage"),
            (VerificationReport, "can_verify_local"),
            (OutcomeEvidence, "can_verify_outcome"),
            (WorkReport, "can_mutate_artifacts"),
        )
        for result_type, required_capability in requirements:
            if (
                isinstance(result, result_type)
                and required_capability not in capabilities
            ):
                raise InvocationRejected("authority_violation")


def new_submission_time() -> datetime:
    """The sole timestamp helper used by future stage services when creating context."""
    return datetime.now(UTC)
