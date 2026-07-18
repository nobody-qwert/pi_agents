"""Contract tests for the required LM Studio model adapter."""

from __future__ import annotations

import json
from typing import Literal
from urllib.error import URLError

import pytest

from orchestrator.model_gateway import (
    GatewayFailure,
    HttpResponse,
    LmStudioGateway,
    ModelRequest,
)
from orchestrator.settings import AppSettings


class FakeTransport:
    def __init__(self, result: HttpResponse | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        *,
        method: Literal["GET", "POST"],
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Cancelled:
    def is_cancelled(self) -> bool:
        return True


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "app_env": "test",
            "app_base_url": "http://localhost:3000",
            "database_url": "postgresql+psycopg://test:test@postgres/test",
            "model_provider": "lm-studio",
            "lm_studio_base_url": "http://lm-studio:1234/v1",
            "lm_studio_api_key": "not-a-real-secret",
            "lm_studio_model_id": "qwen3.6-27b",
        }
    )


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (HttpResponse(200, b'{"data":[{"id":"qwen3.6-27b"}]}'), "ready"),
        (HttpResponse(200, b'{"data":[{"id":"another-model"}]}'), "model_unavailable"),
        (HttpResponse(200, b'{"data":"not-a-list"}'), "malformed"),
        (HttpResponse(200, b'{"data":[{}]}'), "malformed"),
        (HttpResponse(502, b""), "unreachable"),
    ],
)
def test_readiness_is_specific_and_never_exposes_credentials(
    response: HttpResponse, status: str
) -> None:
    transport = FakeTransport(response)
    result = LmStudioGateway(settings(), transport=transport).readiness()
    assert result.status == status
    assert "not-a-real-secret" not in str(result)
    assert transport.calls[0]["url"] == "http://lm-studio:1234/v1/models"


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError()])
def test_readiness_maps_transport_failures_to_unreachable(error: Exception) -> None:
    assert (
        LmStudioGateway(settings(), transport=FakeTransport(error)).readiness().status
        == "unreachable"
    )


def test_completion_checks_cancellation_before_sending_a_request() -> None:
    transport = FakeTransport(HttpResponse(200, b"{}"))
    gateway = LmStudioGateway(settings(), transport=transport)
    with pytest.raises(GatewayFailure) as captured:
        gateway.complete(
            ModelRequest(
                agent_id="a",
                system_prompt="s",
                user_prompt="u",
                max_output_tokens=1,
                temperature=0,
            ),
            cancellation=Cancelled(),
        )
    assert captured.value.code == "cancelled"
    assert captured.value.retryable is False
    assert transport.calls == []


def test_completion_returns_typed_response_and_keeps_api_key_inside_header() -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            b'{"model":"qwen3.6-27b","choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}',
        )
    )
    result = LmStudioGateway(settings(), transport=transport).complete(
        ModelRequest(
            agent_id="executor",
            system_prompt="produce JSON",
            user_prompt="do the task",
            max_output_tokens=100,
            temperature=0,
        )
    )
    assert result.content == "{}"
    raw_body = transport.calls[0]["body"]
    assert isinstance(raw_body, bytes)
    body = json.loads(raw_body)
    assert body["model"] == "qwen3.6-27b"
    assert "not-a-real-secret" not in body.values()


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (429, "retryable_provider_error", True),
        (500, "retryable_provider_error", True),
        (401, "terminal_provider_error", False),
    ],
)
def test_provider_failures_have_bounded_retry_classification(
    status_code: int, code: str, retryable: bool
) -> None:
    gateway = LmStudioGateway(
        settings(), transport=FakeTransport(HttpResponse(status_code, b"{}"))
    )
    with pytest.raises(GatewayFailure) as captured:
        gateway.complete(
            ModelRequest(
                agent_id="a",
                system_prompt="s",
                user_prompt="u",
                max_output_tokens=1,
                temperature=0,
            )
        )
    assert captured.value.code == code
    assert captured.value.retryable is retryable
