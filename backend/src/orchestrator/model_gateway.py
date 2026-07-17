"""Typed, provider-specific boundary for the required LM Studio model service.

The rest of the application depends on :class:`ModelGateway`, never on an HTTP
client or an OpenAI SDK.  This module deliberately exposes no provider fallback:
the configured LM Studio model must be available before a run can execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import Field

from orchestrator.domain.primitives import NonEmptyStr, StrictDomainModel
from orchestrator.settings import AppSettings


class GatewayFailure(Exception):
    """A safe, typed failure returned by the model boundary."""

    def __init__(
        self,
        code: Literal[
            "cancelled",
            "unreachable",
            "timeout",
            "malformed_response",
            "model_unavailable",
            "retryable_provider_error",
            "terminal_provider_error",
        ],
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        super().__init__(code)


class CancellationToken(Protocol):
    """Minimal token so callers can stop work before another HTTP attempt."""

    def is_cancelled(self) -> bool: ...


class ModelRequest(StrictDomainModel):
    """Bounded request accepted by the provider-neutral gateway."""

    agent_id: NonEmptyStr
    system_prompt: NonEmptyStr
    user_prompt: NonEmptyStr
    max_output_tokens: int = Field(ge=1, le=16_384)
    temperature: float = Field(ge=0, le=2)


class ModelResponse(StrictDomainModel):
    """The content and non-sensitive provider metadata used by invocation code."""

    content: str
    model_id: NonEmptyStr
    finish_reason: str | None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class ModelReadiness(StrictDomainModel):
    status: Literal["ready", "unreachable", "malformed", "model_unavailable"]
    configured_model_id: NonEmptyStr


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        *,
        method: Literal["GET", "POST"],
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class ModelGateway(Protocol):
    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness: ...

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse: ...


class UrllibHttpTransport:
    """Small standard-library transport; credentials remain inside this adapter."""

    def request(
        self,
        *,
        method: Literal["GET", "POST"],
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(status_code=response.status, body=response.read())
        except HTTPError as error:
            return HttpResponse(status_code=error.code, body=error.read())


class LmStudioGateway:
    """LM Studio's OpenAI-compatible adapter with no implicit retry loop."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = str(settings.lm_studio_base_url).rstrip("/")
        self._api_key = settings.lm_studio_api_key
        self._model_id = settings.lm_studio_model_id
        self._transport = transport or UrllibHttpTransport()
        self._timeout_seconds = timeout_seconds

    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        try:
            response = self._request(
                method="GET", path="models", body=None, cancellation=cancellation
            )
        except GatewayFailure as error:
            if error.code in {"unreachable", "timeout", "cancelled"}:
                return ModelReadiness(
                    status="unreachable", configured_model_id=self._model_id
                )
            raise
        if not 200 <= response.status_code < 300:
            return ModelReadiness(
                status="unreachable", configured_model_id=self._model_id
            )
        try:
            payload = json.loads(response.body)
            models = payload["data"]
            if not isinstance(models, list):
                raise ValueError("invalid models response")
            model_ids: set[str] = set()
            for item in models:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("invalid model entry")
                model_ids.add(item["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ModelReadiness(
                status="malformed", configured_model_id=self._model_id
            )
        if self._model_id not in model_ids:
            return ModelReadiness(
                status="model_unavailable", configured_model_id=self._model_id
            )
        return ModelReadiness(status="ready", configured_model_id=self._model_id)

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        payload = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        response = self._request(
            method="POST",
            path="chat/completions",
            body=json.dumps(payload).encode("utf-8"),
            cancellation=cancellation,
        )
        self._raise_for_provider_status(response.status_code)
        try:
            decoded = json.loads(response.body)
            choice = decoded["choices"][0]
            content = choice["message"]["content"]
            model_id = decoded["model"]
            usage = decoded.get("usage", {})
            if not isinstance(content, str) or not isinstance(model_id, str):
                raise ValueError("invalid completion response")
            return ModelResponse(
                content=content,
                model_id=model_id,
                finish_reason=choice.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise GatewayFailure("malformed_response", retryable=False) from error

    def _request(
        self,
        *,
        method: Literal["GET", "POST"],
        path: str,
        body: bytes | None,
        cancellation: CancellationToken | None,
    ) -> HttpResponse:
        if cancellation is not None and cancellation.is_cancelled():
            raise GatewayFailure("cancelled", retryable=False)
        try:
            return self._transport.request(
                method=method,
                url=f"{self._base_url}/{path}",
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except TimeoutError as error:
            raise GatewayFailure("timeout", retryable=True) from error
        except (URLError, OSError) as error:
            raise GatewayFailure("unreachable", retryable=True) from error

    @staticmethod
    def _raise_for_provider_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 404:
            raise GatewayFailure(
                "model_unavailable", retryable=False, http_status=status_code
            )
        if status_code in {408, 409, 425, 429} or status_code >= 500:
            raise GatewayFailure(
                "retryable_provider_error", retryable=True, http_status=status_code
            )
        raise GatewayFailure(
            "terminal_provider_error", retryable=False, http_status=status_code
        )
