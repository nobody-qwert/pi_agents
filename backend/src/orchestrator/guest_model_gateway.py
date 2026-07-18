"""Model-gateway facade that runs delivery roles through guest Pi RPC."""

from __future__ import annotations

from typing import Protocol, cast

from orchestrator.model_gateway import (
    CancellationToken,
    GatewayFailure,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.pi_rpc import PiRole, PiRpcError, PiRpcResult
from orchestrator.vm import GuestHandle, VmLifecycleError


class PiInvocationPort(Protocol):
    def invoke(
        self, *, guest: GuestHandle, guest_path: str, role: PiRole, prompt: str
    ) -> PiRpcResult: ...


class GuestPiModelGateway:
    """Adapts a run-bound Pi session to the shared structured invocation layer."""

    _ROLES = frozenset({"executor", "local-verifier", "integrator", "outcome-verifier"})

    def __init__(
        self,
        port: PiInvocationPort,
        *,
        guest: GuestHandle,
        guest_path: str,
        model_id: str,
    ) -> None:
        self._port = port
        self._guest = guest
        self._guest_path = guest_path
        self._model_id = model_id

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        if cancellation is not None and cancellation.is_cancelled():
            return ModelReadiness(
                status="unreachable", configured_model_id=self._model_id
            )
        return ModelReadiness(status="ready", configured_model_id=self._model_id)

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        if cancellation is not None and cancellation.is_cancelled():
            raise GatewayFailure("cancelled", retryable=False)
        if request.agent_id not in self._ROLES:
            raise GatewayFailure("terminal_provider_error", retryable=False)
        prompt = (
            "<system>\n"
            + request.system_prompt
            + "\n</system>\n<task>\n"
            + request.user_prompt
            + "\n</task>"
        )
        try:
            result = self._port.invoke(
                guest=self._guest,
                guest_path=self._guest_path,
                role=cast(PiRole, request.agent_id),
                prompt=prompt,
            )
        except PiRpcError as error:
            if error.code == "pi_runtime_timeout":
                raise GatewayFailure("timeout", retryable=True) from error
            if error.retryable:
                raise GatewayFailure("unreachable", retryable=True) from error
            raise GatewayFailure("terminal_provider_error", retryable=False) from error
        except VmLifecycleError as error:
            raise GatewayFailure("unreachable", retryable=True) from error
        return ModelResponse(
            content=result.text,
            model_id=self._model_id,
            finish_reason="stop",
            prompt_tokens=None,
            completion_tokens=None,
        )
